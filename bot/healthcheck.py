"""Проверка работоспособности: python -m bot.healthcheck

Код выхода 0 — всё в порядке, 1 — проблема (используется Docker healthcheck).
Проверяет: планировщик жив (свежий пульс), PostgreSQL и Redis отвечают.
"""
from __future__ import annotations

import asyncio
import sys
import time

from bot.health import STALE_AFTER_SECONDS, heartbeat_age, read_heartbeat
from core.config import mask_url, settings


async def _check_db() -> str | None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=5)
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        await engine.dispose()


async def _check_redis() -> str | None:
    try:
        from redis.asyncio import Redis

        r = Redis.from_url(settings.redis_url, socket_connect_timeout=5)
        try:
            await r.ping()
        finally:
            await r.aclose()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


async def run(quiet: bool = False) -> int:
    problems: list[str] = []
    hb = read_heartbeat()
    age = heartbeat_age(hb)
    if age is None:
        problems.append("бот ещё не записал пульс (не запущен?)")
    elif age > STALE_AFTER_SECONDS:
        problems.append(f"пульс устарел: {age:.0f} с назад")

    db_err, redis_err = await asyncio.gather(_check_db(), _check_redis())
    if db_err:
        problems.append(f"PostgreSQL ({mask_url(settings.database_url)}): {db_err}")
    if redis_err:
        problems.append(f"Redis: {redis_err} — бот работает, но диалоги хранятся в памяти")

    if not quiet:
        print("Пульс бота:", "нет" if age is None else f"{age:.0f} с назад")
        for code, s in ((hb or {}).get("sources") or {}).items():
            ok = s.get("last_ok_at")
            ok_txt = f"{time.time() - ok:.0f} с назад" if ok else "ещё не было"
            err = f", ошибка: {s['last_error']}" if s.get("last_error") else ""
            print(f"  источник {s.get('name', code)}: успешный опрос {ok_txt}, новых заказов {s.get('jobs_new', 0)}{err}")
        print("PostgreSQL:", "OK" if not db_err else "ОШИБКА")
        print("Redis:", "OK" if not redis_err else "недоступен")
        print("ИТОГ:", "OK" if not [p for p in problems if not p.startswith("Redis")] else "ПРОБЛЕМА")
        for p in problems:
            print("  -", p)
    # Redis необязателен: без него бот работает (FSM в памяти)
    critical = [p for p in problems if not p.startswith("Redis")]
    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(quiet="--quiet" in sys.argv)))
