from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из корня проекта
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


class Settings:
    def __init__(self) -> None:
        # Telegram
        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

        # Database & Cache
        self.database_url: str = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://fr_user:fr_password@localhost:5432/freelanceradar"
        )
        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        # LLM (OpenRouter)
        self.openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
        self.openrouter_base_url: str = os.getenv(
            "OPENROUTER_BASE_URL",
            "https://openrouter.ai/api/v1"
        )
        self.llm_model: str = os.getenv("LLM_MODEL", "anthropic/claude-3.5-sonnet")

        # Sources (задел на будущее)
        self.source_a_base_url: str = os.getenv("SOURCE_A_BASE_URL", "")
        self.source_a_token: str = os.getenv("SOURCE_A_TOKEN", "")

        # Config
        self.poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
        self.llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "8"))


settings = Settings()
