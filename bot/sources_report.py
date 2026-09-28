"""Выгрузка реестра источников из базы: python -m bot.sources_report [--write]

Показывает все источники, включая добавленные пользователями из бота:
тип, адрес, интервал, статус, кто и когда добавил, число заказов и подписчиков.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import ROOT_DIR, settings
from db.models import Job, ProfileSource, Source

KIND = {"rss": "RSS/Atom", "page": "страница сайта", None: "встроенный / API"}


async def build() -> str:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            jobs = dict((await conn.execute(select(Job.source_id, func.count()).group_by(Job.source_id))).all())
            subs = dict(
                (await conn.execute(select(ProfileSource.source_id, func.count()).group_by(ProfileSource.source_id))).all()
            )
            rows = (await conn.execute(select(Source).order_by(Source.id))).all()
    finally:
        await engine.dispose()

    lines = [
        "# Реестр источников (выгрузка из базы)",
        "",
        f"Сформировано: {datetime.now():%d.%m.%Y %H:%M}. Правила подключения — в SOURCES.md.",
        "",
        "| # | Название | Код | Тип | Адрес | Интервал, с | Статус | Добавил (Telegram ID) | Добавлен | Заказов | Подписчиков |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (s,) in rows:
        lines.append(
            f"| {s.id} | {s.name} | `{s.code}` | {KIND.get(s.feed_kind, s.feed_kind)} | {s.feed_url or '—'} | "
            f"{s.poll_interval_sec} | {s.status.value} | {s.added_by or 'команда'} | {s.created_at:%d.%m.%Y} | "
            f"{jobs.get(s.id, 0)} | {subs.get(s.id, 0)} |"
        )
    lines += [
        "",
        "Источники пользователей подключены после автоматической проверки: robots.txt разрешает чтение, "
        "сайт не закрыт для ботов (нет 401/403), адрес внешний, по ссылке есть RSS или список заказов.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    text = asyncio.run(build())
    if "--write" in sys.argv:
        path = ROOT_DIR / "sources_registry.md"
        path.write_text(text, encoding="utf-8")
        print(f"Записано: {path}")
    else:
        print(text)


if __name__ == "__main__":
    main()
