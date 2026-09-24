from __future__ import annotations

from typing import Optional

from .base import BaseAdapter, FetchResult


class DummyAdapter(BaseAdapter):
    source_code = "dummy"

    async def fetch_new(self, cursor: Optional[str]) -> FetchResult:
        items = [
            {
                "external_id": "dummy-1",
                "url": "https://example.com/jobs/dummy-1",
                "title": "Landing page design",
                "description": "Need a landing page for a course",
                "budget_min": 20000,
                "budget_max": 35000,
                "currency": "RUB",
                "published_at": "2026-09-20T07:01:03Z",
            }
        ]
        return FetchResult(
            source_code=self.source_code,
            cursor=cursor,
            items=items,
            next_cursor="2026-09-20T07:01:03Z",
        )
