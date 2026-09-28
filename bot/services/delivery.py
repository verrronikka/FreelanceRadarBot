"""Отправка карточек заказов в Telegram с повторами (сценарий B, шаг 6)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from html import escape
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.keyboards.main import job_card_keyboard
from db.models import DeliveryStatus, Job, LLMStatus, MatchDecision, UserStatus
from db.repository import get_pending_deliveries

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
SEND_TIMEOUT = 20  # секунд на одну отправку, чтобы зависшее соединение не держало очередь


def fmt_money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def fmt_budget(job: Job) -> str:
    cur = {"RUB": "₽", "UAH": "₴", "USD": "$", "EUR": "€"}.get(job.currency.upper(), job.currency)
    if job.budget_min is not None and job.budget_max is not None and job.budget_min != job.budget_max:
        return f"{fmt_money(job.budget_min)}–{fmt_money(job.budget_max)} {cur}"
    value = job.budget_max if job.budget_max is not None else job.budget_min
    return f"{fmt_money(value)} {cur}" if value is not None else "не указан"


def fmt_published(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    delta = (datetime.now(timezone.utc) - dt).total_seconds()
    if delta < 120:
        return "только что"
    if delta < 3600:
        return f"{int(delta // 60)} мин назад"
    if delta < 86400:
        return f"{int(delta // 3600)} ч назад"
    return dt.strftime("%d.%m.%Y")


def render_card(decision: MatchDecision, header: str = "🔥 Новый заказ") -> str:
    job = decision.job
    reasons = decision.reasons or ""
    if decision.llm_status == LLMStatus.unavailable:
        reasons = f"{reasons} (подобрано по фильтрам)" if reasons else "подобрано по фильтрам"
    lines = [
        f"{escape(header)} · {escape(job.source.name)}",
        "",
        f"<b>{escape(job.title)}</b>",
    ]
    if job.description:
        desc = job.description if len(job.description) <= 300 else job.description[:300].rsplit(" ", 1)[0] + "…"
        lines.append(escape(desc))
    lines += ["", f"💰 {escape(fmt_budget(job))}", f"🕒 {fmt_published(job.published_at)}"]
    if reasons:
        lines.append(f"<blockquote>✨ Почему подходит: {escape(reasons)}</blockquote>")
    return "\n".join(lines)


async def send_pending(bot: Bot, session_maker: async_sessionmaker[AsyncSession]) -> int:
    """Отправляет pending-доставки. Возвращает число отправленных."""
    sent = 0
    async with session_maker() as session:
        deliveries = await get_pending_deliveries(session)
        for delivery in deliveries:
            decision = delivery.decision
            user = decision.profile.user
            if user.status != UserStatus.active or user.notifications_paused:
                delivery.status = DeliveryStatus.cancelled
                delivery.error_code = "USER_INACTIVE_OR_PAUSED"
                continue

            delivery.attempts += 1
            try:
                await bot.send_message(
                    chat_id=user.telegram_user_id,
                    text=render_card(decision),
                    reply_markup=job_card_keyboard(decision.job),
                    request_timeout=SEND_TIMEOUT,
                )
            except TelegramRetryAfter as exc:
                # TELEGRAM_RATE_LIMITED: откладываем согласно retry_after, попытку не считаем
                logger.warning("TELEGRAM_RATE_LIMITED: retry after %s s", exc.retry_after)
                delivery.attempts -= 1
                delivery.error_code = "TELEGRAM_RATE_LIMITED"
                await session.commit()
                await asyncio.sleep(exc.retry_after)
                break
            except TelegramForbiddenError:
                delivery.status = DeliveryStatus.failed
                delivery.error_code = "TELEGRAM_FORBIDDEN"
                user.status = UserStatus.blocked  # пользователь заблокировал бота
            except TelegramBadRequest as exc:
                delivery.status = DeliveryStatus.failed
                delivery.error_code = "TELEGRAM_BAD_REQUEST"
                logger.warning("Delivery %s bad request: %s", delivery.id, exc.message)
            except (TelegramServerError, TelegramNetworkError) as exc:
                delivery.error_code = "TELEGRAM_TEMPORARY_FAILURE"
                if delivery.attempts >= MAX_ATTEMPTS:
                    delivery.status = DeliveryStatus.failed
                logger.warning("Delivery %s temporary error (attempt %s): %s", delivery.id, delivery.attempts, exc)
            else:
                delivery.status = DeliveryStatus.sent
                delivery.sent_at = datetime.now(timezone.utc)
                delivery.error_code = None
                sent += 1
                await asyncio.sleep(0.05)  # мягкий rate limit (~20 msg/s)
            await session.commit()
        await session.commit()
    return sent
