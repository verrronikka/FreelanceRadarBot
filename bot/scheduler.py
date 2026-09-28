"""Фоновый планировщик: опрос источников, дедупликация, matching, доставка.

Документация предлагает Celery Beat + workers. Здесь те же гарантии
(lease в Redis, retry с экспоненциальной паузой, идемпотентный upsert,
уникальные decision/delivery) реализованы асинхронными задачами внутри
процесса бота: Celery официально не поддерживает Windows, а для нагрузки
MVP (2 источника, до 10 000 заказов/сутки) отдельные воркеры не нужны.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.adapters.base import (
    BaseAdapter,
    FetchResult,
    SourceAccessDenied,
    SourceError,
    SourceRateLimited,
    SourceSchemaInvalid,
)
from bot.adapters.freelancehunt import FreelancehuntAdapter
from bot.adapters.html_page import HtmlPageAdapter
from bot.adapters.rss import RssAdapter
from bot.health import write_heartbeat
from bot.services.delivery import send_pending
from bot.services.llm_gateway import LLMGateway
from bot.services.matching import match_job
from core.config import settings
from db.models import Source
from db.repository import (
    create_match_decision,
    create_notification_delivery,
    get_active_sources,
    get_decision,
    get_profiles_for_source,
    pause_source,
    purge_old_job_texts,
    upsert_job,
)

logger = logging.getLogger(__name__)

TICK_SECONDS = 5
SOURCE_RETRIES = 3
CLEANUP_EVERY_SECONDS = 3600
FIRST_POLL_MATCH_LIMIT = 5


@dataclass
class SourceState:
    cursor: Optional[str] = None
    next_poll_at: float = 0.0
    backoff: float = 0.0
    # для /status и healthcheck
    last_ok_at: Optional[float] = None  # unix time
    last_error: Optional[str] = None
    polls: int = 0
    errors: int = 0
    jobs_new: int = 0
    matched: int = 0

    def fail(self, code: str) -> None:
        self.errors += 1
        self.last_error = code


class Scheduler:
    def __init__(
        self,
        bot: Bot,
        session_maker: async_sessionmaker[AsyncSession],
        adapters: dict[str, BaseAdapter],
        llm: Optional[LLMGateway] = None,
        redis: Optional[object] = None,
    ) -> None:
        self.bot = bot
        self.session_maker = session_maker
        self.adapters = adapters
        self.llm = llm
        self.redis = redis  # redis.asyncio.Redis или None
        self.state: dict[str, SourceState] = {}
        self._local_locks: dict[str, asyncio.Lock] = {}
        self._last_cleanup = 0.0
        self.started_at = time.time()
        self.sent_total = 0

    def snapshot(self) -> dict:
        """Состояние планировщика для healthcheck и команды /status."""
        return {
            "ts": time.time(),
            "started_at": self.started_at,
            "sent_total": self.sent_total,
            "llm_enabled": bool(self.llm and self.llm.enabled),
            "sources": {
                code: {
                    "name": getattr(self.adapters.get(code), "source_name", code),
                    "last_ok_at": st.last_ok_at,
                    "last_error": st.last_error,
                    "polls": st.polls,
                    "errors": st.errors,
                    "jobs_new": st.jobs_new,
                    "matched": st.matched,
                }
                for code, st in self.state.items()
            },
        }

    # ---------------------------------------------------------------- lease

    async def _acquire_lease(self, code: str, ttl: int) -> bool:
        """Lease предотвращает параллельный опрос одного источника (сценарий B, шаг 1)."""
        if self.redis is not None:
            try:
                return bool(await self.redis.set(f"fr:lease:poll:{code}", "1", nx=True, ex=ttl))  # type: ignore[attr-defined]
            except Exception as exc:  # Redis пропал — работаем на локальной блокировке
                logger.warning("Redis lease error: %s", exc)
        lock = self._local_locks.setdefault(code, asyncio.Lock())
        if lock.locked():
            return False
        await lock.acquire()
        return True

    async def _release_lease(self, code: str) -> None:
        if self.redis is not None:
            try:
                await self.redis.delete(f"fr:lease:poll:{code}")  # type: ignore[attr-defined]
            except Exception:
                pass
        lock = self._local_locks.get(code)
        if lock is not None and lock.locked():
            lock.release()

    # ---------------------------------------------------------------- polling

    async def _fetch_with_retry(self, adapter: BaseAdapter, cursor: Optional[str]) -> FetchResult:
        delay = 2.0
        for attempt in range(1, SOURCE_RETRIES + 2):
            try:
                return await adapter.fetch_new(cursor)
            except SourceError as exc:
                if not exc.retryable or attempt > SOURCE_RETRIES or isinstance(exc, SourceRateLimited):
                    raise
                logger.warning("%s from %s (attempt %s): %s", exc.code, adapter.source_code, attempt, exc)
                await asyncio.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    async def poll_source(self, source: Source) -> None:
        adapter = self.adapters.get(source.code)
        if adapter is None:
            return
        st = self.state.setdefault(source.code, SourceState())
        interval = max(30, source.poll_interval_sec)
        started = time.monotonic()

        try:
            result = await self._fetch_with_retry(adapter, st.cursor)
        except SourceAccessDenied as exc:
            st.fail(exc.code)
            logger.error("SOURCE_ACCESS_DENIED %s: %s — источник остановлен до ручной проверки", source.code, exc)
            async with self.session_maker() as session:
                await pause_source(session, source.id)
                await session.commit()
            return
        except SourceSchemaInvalid as exc:
            st.fail(exc.code)
            logger.error("SOURCE_SCHEMA_INVALID %s: %s — cursor не изменён", source.code, exc)
            st.next_poll_at = time.monotonic() + interval
            return
        except SourceRateLimited as exc:
            st.fail(exc.code)
            st.backoff = min(max(st.backoff * 2, interval), 3600)
            wait = exc.retry_after or st.backoff
            logger.warning("SOURCE_RATE_LIMITED %s: следующий опрос через %.0f с", source.code, wait)
            st.next_poll_at = time.monotonic() + wait
            return
        except SourceError as exc:
            st.fail(exc.code)
            logger.error("%s %s: %s — ждём следующий плановый цикл", exc.code, source.code, exc)
            st.next_poll_at = time.monotonic() + interval
            return
        except Exception:
            st.fail("SOURCE_TEMPORARY_FAILURE")
            logger.exception("SOURCE_TEMPORARY_FAILURE %s: непредвиденная ошибка адаптера", source.code)
            st.next_poll_at = time.monotonic() + interval
            return

        st.backoff = 0.0
        st.next_poll_at = time.monotonic() + interval

        # Первый опрос ленты: в ней уже могут быть десятки старых заказов.
        # Сохраняем все (чтобы не прислать их позже), а сверяем с профилями только самые свежие.
        match_ids: Optional[set[str]] = None
        if st.cursor is None and isinstance(adapter, (RssAdapter, HtmlPageAdapter, FreelancehuntAdapter)):
            fresh = sorted(
                result.items,
                key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )[:FIRST_POLL_MATCH_LIMIT]
            match_ids = {i.external_id for i in fresh}

        new_jobs = matched = 0
        async with self.session_maker() as session:
            profiles = await get_profiles_for_source(session, source.id)
            for item in result.items:
                job, is_new = await upsert_job(
                    session,
                    source_id=source.id,
                    external_id=item.external_id,
                    url=item.url,
                    title=item.title,
                    description=item.description,
                    budget_min=item.budget_min,
                    budget_max=item.budget_max,
                    currency=item.currency,
                    published_at=item.published_at,
                    raw_hash=item.raw_hash,
                )
                if not is_new:
                    logger.debug("JOB_DUPLICATE %s/%s", source.code, item.external_id)
                    continue
                new_jobs += 1
                if match_ids is not None and item.external_id not in match_ids:
                    continue
                for profile in profiles:
                    if await get_decision(session, profile.id, job.id) is not None:
                        continue
                    res = await match_job(job, profile, list(profile.filter_rules), self.llm)
                    logger.info(
                        "  «%s» -> профиль «%s»: %s (%s)",
                        job.title[:50], profile.name, "ПОДХОДИТ" if res.matched else "не подходит",
                        ", ".join(res.reasons) or "—",
                    )
                    decision = await create_match_decision(
                        session,
                        profile_id=profile.id,
                        job_id=job.id,
                        verdict=res.verdict,
                        score=res.score,
                        reasons=", ".join(res.reasons) if res.reasons else None,
                        llm_status=res.llm_status,
                    )
                    # На паузе delivery не создаются (раздел 6, состояние «Пауза»)
                    if res.matched and not profile.user.notifications_paused:
                        await create_notification_delivery(session, decision.id)
                        matched += 1
            await session.commit()
        # cursor двигаем только после успешной записи в БД
        st.cursor = result.next_cursor
        st.polls += 1
        st.last_ok_at = time.time()
        st.last_error = None
        st.jobs_new += new_jobs
        st.matched += matched
        logger.info(
            "poll %s: получено=%d новых=%d совпадений=%d за %.2f с",
            source.code, len(result.items), new_jobs, matched, time.monotonic() - started,
        )

    async def _poll_guarded(self, source: Source) -> None:
        ttl = max(30, source.poll_interval_sec)
        if not await self._acquire_lease(source.code, ttl):
            return
        try:
            await self.poll_source(source)
        finally:
            await self._release_lease(source.code)

    # ---------------------------------------------------------------- main loop

    def _sync_feed_adapters(self, sources: list[Source]) -> None:
        """Подхватывает RSS-источники, добавленные пользователями из бота."""
        for s in sources:
            if s.feed_url and s.code not in self.adapters:
                if s.feed_kind == "page":
                    self.adapters[s.code] = HtmlPageAdapter(code=s.code, name=s.name, page_url=s.feed_url)
                else:
                    self.adapters[s.code] = RssAdapter(code=s.code, name=s.name, feed_url=s.feed_url)
                logger.info("Подключён источник «%s» (%s): %s", s.name, s.feed_kind or "rss", s.feed_url)

    async def tick(self) -> None:
        async with self.session_maker() as session:
            sources = await get_active_sources(session)
        self._sync_feed_adapters(sources)
        now = time.monotonic()
        due = [s for s in sources if s.code in self.adapters and self.state.get(s.code, SourceState()).next_poll_at <= now]
        # Ошибка одного источника не блокирует остальные
        results = await asyncio.gather(*(self._poll_guarded(s) for s in due), return_exceptions=True)
        for src, res in zip(due, results):
            if isinstance(res, BaseException):
                logger.error("Ошибка опроса %s", src.code, exc_info=res)

        sent = await send_pending(self.bot, self.session_maker)
        self.sent_total += sent
        if sent:
            logger.info("Отправлено уведомлений: %d", sent)
        write_heartbeat(self.snapshot())

        if now - self._last_cleanup > CLEANUP_EVERY_SECONDS:
            self._last_cleanup = now
            async with self.session_maker() as session:
                purged = await purge_old_job_texts(session, settings.job_retention_days)
                await session.commit()
            if purged:
                logger.info("Удалены тексты %d заказов старше %d дней", purged, settings.job_retention_days)

    def poll_soon(self) -> None:
        """Опросить все источники на ближайшем тике (например, сразу после сохранения профиля)."""
        for st in self.state.values():
            st.next_poll_at = 0.0

    async def run(self) -> None:
        logger.info("Scheduler started: источники=%s", ", ".join(self.adapters) or "нет")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler tick error")
            await asyncio.sleep(TICK_SECONDS)
