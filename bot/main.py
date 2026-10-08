"""Точка входа: python -m bot.main"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand, ErrorEvent, TelegramObject
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

import db.models  # noqa: F401  — регистрируем модели в metadata
from bot.adapters.base import BaseAdapter
from bot.adapters.example import ExampleAdapter
from bot.adapters.freelancehunt import FreelancehuntAdapter
from bot.adapters.rss import RssAdapter
from bot.handlers import start
from bot.scheduler import Scheduler
from bot.services.llm_gateway import LLMGateway
from bot.telegram_session import ProxyAwareSession
from core.config import ConfigError, mask_url, settings
from db.base import Base
from db.repository import ensure_source, get_active_keywords

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
if os.getenv("LOG_FILE"):
    # LOG_FILE=logs/bot.log — лог ещё и в файл (до 5 файлов по 5 МБ), для работы без присмотра
    from logging.handlers import RotatingFileHandler

    Path(os.environ["LOG_FILE"]).parent.mkdir(parents=True, exist_ok=True)
    _handlers.append(RotatingFileHandler(os.environ["LOG_FILE"], maxBytes=5_000_000, backupCount=4, encoding="utf-8"))
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, handlers=_handlers)
# httpx на INFO пишет полный URL запроса — там может оказаться токен
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("freelanceradar")


class DatabaseMiddleware(BaseMiddleware):
    """Выдаёт хендлеру AsyncSession на время обработки одного апдейта."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self.session_maker = session_maker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_maker() as session:
            data["session"] = session
            return await handler(event, data)


async def prepare_database(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Колонка, добавленная после первой версии схемы (есть и alembic-миграция)
        for ddl in (
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS notifications_paused BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS feed_url VARCHAR(1024)",
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS added_by BIGINT",
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS feed_kind VARCHAR(16)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_feed_url ON sources (feed_url)",
        ):
            await conn.execute(text(ddl))


def build_adapters(session_maker: async_sessionmaker[AsyncSession]) -> dict[str, BaseAdapter]:
    adapters: dict[str, BaseAdapter] = {}
    if settings.demo_source_enabled:

        async def demo_keywords() -> list[str]:
            async with session_maker() as session:
                return await get_active_keywords(session, ExampleAdapter.source_code)

        a = ExampleAdapter(keyword_provider=demo_keywords)
        adapters[a.source_code] = a
    if settings.freelancehunt_token:
        adapters["freelancehunt"] = FreelancehuntAdapter(
            settings.freelancehunt_token, only_my_skills=settings.freelancehunt_only_my_skills
        )
    if settings.source_a_base_url:
        adapters["source_a"] = RssAdapter(
            code="source_a",
            name=settings.source_a_name,
            feed_url=settings.source_a_base_url,
            token=settings.source_a_token,
        )
    return adapters


async def seed_sources(session_maker: async_sessionmaker[AsyncSession], adapters: dict[str, BaseAdapter]) -> None:
    async with session_maker() as session:
        for adapter in adapters.values():
            source = await ensure_source(session, adapter.source_code, adapter.source_name, settings.poll_interval_seconds)
            source.name = adapter.source_name
            source.poll_interval_sec = max(30, settings.poll_interval_seconds)
        await session.commit()


async def connect_redis() -> Any:
    """Redis нужен для FSM и lease. Если он недоступен — работаем в памяти."""
    try:
        from redis.asyncio import Redis

        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
        await redis.ping()
        logger.info("Redis подключён: %s", mask_url(settings.redis_url))
        return redis
    except Exception as exc:
        logger.warning(
            "Redis недоступен (%s: %s) — состояния диалогов будут храниться в памяти. "
            "Запустите: docker compose up -d", type(exc).__name__, exc
        )
        return None


async def main() -> int:
    try:
        settings.validate()
    except ConfigError as exc:
        logger.error("Ошибка в .env:\n%s", exc)
        return 1

    # ---- Telegram
    proxy = settings.telegram_proxy_url
    logger.info("Прокси для Telegram: %s", mask_url(proxy) if proxy else "не используется")
    session = ProxyAwareSession(proxy_url=proxy)
    bot = Bot(
        token=settings.telegram_bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True),
    )
    try:
        me = await asyncio.wait_for(bot.me(), timeout=30)  # bot.me() кэширует ответ — polling не будет спрашивать повторно
    except Exception as exc:
        logger.error("Не удалось подключиться к Telegram API: %s", exc)
        if "Unauthorized" in str(exc):
            logger.error("Токен неверный — проверьте TELEGRAM_BOT_TOKEN (берётся у @BotFather).")
        elif proxy:
            logger.error(
                "Проверьте, что VPN-клиент (Happ) запущен и слушает %s. "
                "Если не работает http — попробуйте TELEGRAM_PROXY_URL=socks5://127.0.0.1:10808. "
                "Диагностика: python check_telegram_proxy.py", mask_url(proxy),
            )
        else:
            logger.error("api.telegram.org недоступен напрямую — укажите TELEGRAM_PROXY_URL в .env")
        await bot.session.close()
        return 1
    logger.info("Telegram: подключён как @%s", me.username)

    # ---- PostgreSQL
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        await prepare_database(engine)
    except Exception as exc:
        logger.error("Не удалось подключиться к PostgreSQL (%s): %s", mask_url(settings.database_url), exc)
        logger.error("Запустите базу: docker compose up -d  (и проверьте DATABASE_URL в .env)")
        await bot.session.close()
        await engine.dispose()
        return 1
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    logger.info("PostgreSQL: таблицы готовы")

    adapters = build_adapters(session_maker)
    await seed_sources(session_maker, adapters)
    if not adapters:
        logger.warning("Нет ни одного источника: включите DEMO_SOURCE_ENABLED или задайте SOURCE_A_BASE_URL")

    # ---- Redis / FSM
    redis = await connect_redis()
    storage: BaseStorage
    if redis is not None:
        from aiogram.fsm.storage.redis import RedisStorage

        storage = RedisStorage(redis=redis)
    else:
        storage = MemoryStorage()

    # ---- LLM
    llm = LLMGateway()
    logger.info(
        "ИИ-фильтр: %s", f"{settings.openrouter_base_url.split('//')[-1].split('/')[0]}, модель {settings.llm_model}" if llm.enabled else "ключ не задан — только правила"
    )

    dp = Dispatcher(storage=storage)
    dp.include_router(start.router)
    db_mw = DatabaseMiddleware(session_maker)
    dp.message.middleware(db_mw)
    dp.callback_query.middleware(db_mw)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        logger.exception("Ошибка при обработке апдейта: %s", event.exception, exc_info=event.exception)
        upd = event.update
        target = upd.message or (upd.callback_query.message if upd.callback_query else None)
        if target is not None:
            try:
                await target.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.")
            except Exception:
                pass
        return True

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="menu", description="Показать меню"),
            BotCommand(command="test", description="Прислать тестовое уведомление"),
            BotCommand(command="status", description="Состояние бота"),
            BotCommand(command="delete", description="Удалить профиль"),
            BotCommand(command="help", description="Помощь"),
        ]
    )

    scheduler = Scheduler(bot, session_maker, adapters, llm=llm, redis=redis)
    dp["scheduler"] = scheduler  # доступен в хендлерах как аргумент scheduler
    scheduler_task = asyncio.create_task(scheduler.run(), name="scheduler")

    logger.info("Бот запущен. Откройте @%s в Telegram и нажмите /start. Остановка — Ctrl+C", me.username)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        # Сбой связи с Telegram (например, VPN переподключается) не должен ронять бота:
        # ждём и запускаем polling снова. Выход — Ctrl+C.
        while True:
            try:
                await dp.start_polling(bot)
                break
            except TelegramNetworkError as exc:
                logger.warning("Нет связи с Telegram (%s) — повтор через 15 с. Проверьте VPN/Happ.", exc)
                await asyncio.sleep(15)
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except (asyncio.CancelledError, Exception):
            pass
        for adapter in adapters.values():
            await adapter.close()
        await llm.close()
        await storage.close()
        await bot.session.close()
        await engine.dispose()
        logger.info("Бот остановлен")
    return 0


def run() -> None:
    try:
        code = asyncio.run(main())
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    run()
