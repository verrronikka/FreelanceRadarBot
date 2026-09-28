"""Общие настройки тестов. Тесты не ходят в интернет, в Telegram и в базу:
сеть подменяется httpx.MockTransport, база и бот — простыми заглушками."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Чтобы config не требовал настоящий .env при импорте модулей
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:test")
os.environ["HEALTH_FILE"] = str(ROOT / "data" / "health-test.json")
