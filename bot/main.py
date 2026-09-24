import asyncio
import logging
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import settings
from bot.handlers import start

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, session_maker):
        self.session_maker = session_maker
        super().__init__()

    async def __call__(self, handler, event: Update, data: dict):
        async with self.session_maker() as session:
            data["session"] = session
            return await handler(event, data)


async def main():
    bot = Bot(token=settings.telegram_bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(start.router)

    engine = create_async_engine(settings.database_url, echo=False)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

    dp.message.middleware(DatabaseMiddleware(async_session_maker))
    dp.callback_query.middleware(DatabaseMiddleware(async_session_maker))

    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())