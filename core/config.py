"""Настройки приложения. Все значения берутся из переменных окружения / файла .env.

Допустимые диапазоны проверяются при старте (раздел 4.3 документации).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


class ConfigError(Exception):
    """Ошибка конфигурации — бот не должен стартовать."""


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть целым числом, сейчас: {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "да"}


def _str(name: str, default: str = "") -> str:
    # Убираем пробелы и случайные кавычки вокруг значения
    return os.getenv(name, default).strip().strip('"').strip("'")


class Settings:
    def __init__(self) -> None:
        # Telegram
        self.telegram_bot_token: str = _str("TELEGRAM_BOT_TOKEN")
        self.telegram_proxy_url: str | None = _str("TELEGRAM_PROXY_URL") or None

        # Database & Cache
        self.database_url: str = _str(
            "DATABASE_URL",
            "postgresql+asyncpg://fr_user:fr_password@127.0.0.1:5432/freelanceradar",
        )
        self.redis_url: str = _str("REDIS_URL", "redis://127.0.0.1:6379/0")

        # LLM (OpenRouter)
        self.openrouter_api_key: str = _str("OPENROUTER_API_KEY")
        self.openrouter_base_url: str = _str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self.llm_model: str = _str("LLM_MODEL", "anthropic/claude-3.5-sonnet")
        # Прокси для OpenRouter (если нужен). По умолчанию — тот же, что и для Telegram.
        self.llm_proxy_url: str | None = _str("LLM_PROXY_URL") or self.telegram_proxy_url

        # Источники
        self.demo_source_enabled: bool = _bool("DEMO_SOURCE_ENABLED", True)
        self.source_a_base_url: str = _str("SOURCE_A_BASE_URL")  # URL RSS-ленты разрешённого источника
        self.source_a_name: str = _str("SOURCE_A_NAME", "Источник A")
        self.source_a_token: str = _str("SOURCE_A_TOKEN")

        # Freelancehunt (официальный API v2): токен на https://freelancehunt.com/my/api2
        self.freelancehunt_token: str = _str("FREELANCEHUNT_TOKEN")
        self.freelancehunt_only_my_skills: bool = _bool("FREELANCEHUNT_ONLY_MY_SKILLS", False)

        # Эксплуатационные параметры
        self.poll_interval_seconds: int = _int("POLL_INTERVAL_SECONDS", 60)
        self.llm_timeout_seconds: int = _int("LLM_TIMEOUT_SECONDS", 8)
        self.job_retention_days: int = _int("JOB_RETENTION_DAYS", 30)

    @property
    def llm_configured(self) -> bool:
        key = self.openrouter_api_key
        return bool(key) and not key.startswith("your_")

    def validate(self) -> None:
        errors: list[str] = []
        token = self.telegram_bot_token
        if not token or ":" not in token or token.startswith("123456:"):
            errors.append("TELEGRAM_BOT_TOKEN не задан или это пример из .env.example — возьмите токен у @BotFather")
        if not self.database_url.startswith("postgresql+asyncpg://"):
            errors.append("DATABASE_URL должен начинаться с postgresql+asyncpg://")
        if self.poll_interval_seconds < 30:
            errors.append("POLL_INTERVAL_SECONDS должен быть не меньше 30 (правило документации)")
        if not 1 <= self.llm_timeout_seconds <= 60:
            errors.append("LLM_TIMEOUT_SECONDS должен быть от 1 до 60")
        if not 1 <= self.job_retention_days <= 30:
            errors.append("JOB_RETENTION_DAYS должен быть от 1 до 30")
        if self.telegram_proxy_url and "://" not in self.telegram_proxy_url:
            errors.append("TELEGRAM_PROXY_URL должен быть вида http://127.0.0.1:10809 или socks5://127.0.0.1:10808")
        if errors:
            raise ConfigError("\n".join(f"  • {e}" for e in errors))


def mask_url(url: str | None) -> str:
    """Скрывает логин/пароль в URL, чтобы не писать секреты в логи."""
    if not url:
        return "—"
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


settings = Settings()
