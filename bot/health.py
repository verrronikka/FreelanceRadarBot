"""Файл-пульс планировщика: бот раз в тик записывает своё состояние.

Его читают `python -m bot.healthcheck` (проверка для Docker/сервера) и команда /status.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from core.config import ROOT_DIR

HEALTH_FILE = Path(os.getenv("HEALTH_FILE", str(ROOT_DIR / "data" / "health.json")))
STALE_AFTER_SECONDS = 180  # тик раз в 5 с; 3 минуты тишины — бот завис или упал

logger = logging.getLogger(__name__)


def write_heartbeat(data: dict[str, Any]) -> None:
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEALTH_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, HEALTH_FILE)  # атомарная замена — healthcheck не прочитает половину файла
    except OSError as exc:
        logger.warning("Не удалось записать %s: %s", HEALTH_FILE, exc)


def read_heartbeat() -> Optional[dict[str, Any]]:
    try:
        return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def heartbeat_age(data: Optional[dict[str, Any]]) -> Optional[float]:
    if not data or "ts" not in data:
        return None
    return time.time() - float(data["ts"])
