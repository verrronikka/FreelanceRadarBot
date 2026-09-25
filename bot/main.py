from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from bot.handlers import start
from bot.scheduler import Scheduler
from db.base import Base
import db.models  # noqa: F401  # ensure models are registered

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self.session_maker = session_maker
        super().__init__()

    async def __call__(
        self,
        handler: Any,
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_maker() as session:
            data["session"] = session
            return await handler(event, data)


async def on_startup() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    logger.info("Database tables are ready")


async def main() -> None:
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN не задан. Проверьте .env файл.")
        return

    bot = Bot(token=settings.telegram_bot_token, proxy=settings.telegram_proxy_url)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(start.router)

    engine = create_async_engine(settings.database_url, echo=False)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

    dp.message.middleware(DatabaseMiddleware(async_session_maker))
    dp.callback_query.middleware(DatabaseMiddleware(async_session_maker))

    @dp.errors.register
    async def error_handler(update: Update, exception: Exception) -> bool:
        logger.exception("Update %s caused error %s", update, exception)
        return True

    try:
        await on_startup()
    except Exception as e:
        logger.error("Не удалось подключиться к базе данных: %s", e)
        logger.error(
            "Проверьте, что PostgreSQL запущен и доступен по адресу %s",
            settings.database_url,
        )
        await bot.session.close()
        return

    # Проверяем доступность Telegram API до запуска поллинга
    try:
        me = await bot.get_me(request_timeout=10)
        logger.info("Bot connected as @%s", me.username)
    except Exception as e:
        logger.error("Не удалось подключиться к Telegram API: %s", e)
        logger.error(
            "Проверьте доступ к api.telegram.org или настройте TELEGRAM_PROXY_URL в .env"
        )
        await bot.session.close()
        return

    scheduler = Scheduler(async_session_maker, settings.poll_interval_seconds)
    scheduler_task = asyncio.create_task(scheduler.run())

    logger.info("Starting bot...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.exception("Bot stopped due to error: %s", e)
        logger.error(
            "Если ошибка связана с подключением к api.telegram.org, "
            "проверьте доступ к интернету или настройте TELEGRAM_PROXY_URL в .env"
        )
    finally:
        scheduler_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
