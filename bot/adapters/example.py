from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bot.adapters.types import BaseAdapter, FetchResult


class ExampleAdapter(BaseAdapter):
    source_code = "example"

    def __init__(self) -> None:
        self._cursor: Optional[str] = None
        self._jobs = [
            {
                "external_id": "1",
                "url": "https://example.com/jobs/1",
                "title": "Landing page design",
                "description": "Need a landing page for a course",
                "budget_min": 20000,
                "budget_max": 35000,
                "currency": "RUB",
                "published_at": datetime(2026, 9, 20, 7, 1, 3, tzinfo=timezone.utc),
            },
            {
                "external_id": "2",
                "url": "https://example.com/jobs/2",
                "title": "Python backend developer",
                "description": "FastAPI, PostgreSQL",
                "budget_min": 50000,
                "budget_max": 80000,
                "currency": "RUB",
                "published_at": datetime(2026, 9, 20, 7, 2, 0, tzinfo=timezone.utc),
            },
        ]

    async def fetch_new(self, cursor: Optional[str]) -> FetchResult:
        # В реальном адаптере здесь был бы запрос к API источника.
        # Для демонстрации отдаём тестовые заказы один раз.
        if cursor is not None:
            return FetchResult(
                source_code=self.source_code,
                cursor=cursor,
                items=[],
                next_cursor=cursor,
            )
        next_cursor = "done"
        return FetchResult(
            source_code=self.source_code,
            cursor=cursor,
            items=self._jobs,
            next_cursor=next_cursor,
        )
