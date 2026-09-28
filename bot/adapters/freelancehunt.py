"""Freelancehunt через официальный API v2 (https://apidocs.freelancehunt.com).

Токен выдаётся на https://freelancehunt.com/my/api2 и задаётся в .env:
    FREELANCEHUNT_TOKEN=...
    FREELANCEHUNT_ONLY_MY_SKILLS=false   # true — только проекты по навыкам из вашего профиля на сайте
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

import httpx

from bot.adapters.base import (
    BaseAdapter,
    FetchResult,
    NormalizedJob,
    SourceAccessDenied,
    SourceError,
    SourceRateLimited,
    SourceSchemaInvalid,
    SourceTemporaryFailure,
    make_job,
)

API_URL = "https://api.freelancehunt.com/v2/projects"
_TAG_RE = re.compile(r"<[^>]+>")


def _dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_project(p: dict[str, Any]) -> Optional[NormalizedJob]:
    attrs = p.get("attributes") or {}
    pid = str(p.get("id") or "")
    links = p.get("links") or {}
    self_link = links.get("self")
    web = self_link.get("web") if isinstance(self_link, dict) else None
    url = web or (f"https://freelancehunt.com/project/{pid}.html" if pid else "")
    desc = attrs.get("description") or _TAG_RE.sub(" ", attrs.get("description_html") or "")
    skills = [s.get("name") for s in attrs.get("skills") or [] if isinstance(s, dict) and s.get("name")]
    if skills:
        desc = f"{desc}\nНавыки: {', '.join(skills)}"
    budget = attrs.get("budget") or {}
    amount = budget.get("amount") if isinstance(budget, dict) else None
    currency = (budget.get("currency") if isinstance(budget, dict) else None) or "UAH"
    try:
        return make_job(
            external_id=pid or None,
            url=url,
            title=attrs.get("name") or "",
            description=desc,
            budget_min=int(amount) if isinstance(amount, (int, float)) else None,
            currency=currency,
            published_at=_dt(attrs.get("published_at")),
        )
    except SourceError:
        return None


class FreelancehuntAdapter(BaseAdapter):
    source_code = "freelancehunt"
    source_name = "Freelancehunt"

    def __init__(self, token: str, only_my_skills: bool = False) -> None:
        self.only_my_skills = only_my_skills
        self._client = httpx.AsyncClient(
            timeout=20,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_new(self, cursor: Optional[str]) -> FetchResult:
        params = {"filter[only_my_skills]": "1"} if self.only_my_skills else {}
        try:
            resp = await self._client.get(API_URL, params=params)
        except httpx.HTTPError as exc:
            raise SourceTemporaryFailure(f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise SourceAccessDenied(f"HTTP {resp.status_code} — проверьте FREELANCEHUNT_TOKEN")
        if resp.status_code == 429:
            raise SourceRateLimited("HTTP 429")
        if resp.status_code >= 500:
            raise SourceTemporaryFailure(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise SourceSchemaInvalid(f"HTTP {resp.status_code}")
        try:
            data = resp.json().get("data") or []
        except ValueError as exc:
            raise SourceSchemaInvalid("не JSON") from exc
        items = [j for j in (_parse_project(p) for p in data if isinstance(p, dict)) if j is not None]
        return FetchResult(source_code=self.source_code, cursor=cursor, items=items, next_cursor=cursor)
