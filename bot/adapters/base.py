"""Единый контракт адаптера (раздел 4.1 документации).

Адаптер только получает и нормализует данные источника: он не фильтрует
заказы и не общается с Telegram.
"""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class SourceError(Exception):
    """Ошибка источника с кодом из таблицы 4.1."""

    code = "SOURCE_TEMPORARY_FAILURE"
    retryable = True

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message or self.code)
        self.retry_after = retry_after


class SourceSchemaInvalid(SourceError):
    code = "SOURCE_SCHEMA_INVALID"
    retryable = False


class SourceAccessDenied(SourceError):
    code = "SOURCE_ACCESS_DENIED"
    retryable = False


class SourceRateLimited(SourceError):
    code = "SOURCE_RATE_LIMITED"


class SourceTemporaryFailure(SourceError):
    code = "SOURCE_TEMPORARY_FAILURE"


@dataclass
class NormalizedJob:
    external_id: str
    url: str
    title: str
    description: Optional[str] = None
    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    currency: str = "RUB"
    published_at: Optional[datetime] = None
    raw_hash: Optional[str] = None


@dataclass
class FetchResult:
    source_code: str
    cursor: Optional[str]
    items: list[NormalizedJob] = field(default_factory=list)
    next_cursor: Optional[str] = None


class BaseAdapter(ABC):
    source_code: str
    source_name: str

    @abstractmethod
    async def fetch_new(self, cursor: Optional[str]) -> FetchResult:
        ...

    async def close(self) -> None:  # pragma: no cover
        return None


# ------------------------------------------------------------------ нормализация

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def normalize_text(value: Optional[str]) -> Optional[str]:
    """HTML -> plain text, Unicode NFC, схлопывание пробелов."""
    if value is None:
        return None
    text = html.unescape(_TAG_RE.sub(" ", value))
    text = unicodedata.normalize("NFC", text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def stable_hash(*parts: Optional[str]) -> str:
    joined = "\x1f".join(p or "" for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def make_job(
    *,
    url: str,
    title: str,
    external_id: Optional[str] = None,
    description: Optional[str] = None,
    budget_min: Optional[int] = None,
    budget_max: Optional[int] = None,
    currency: str = "RUB",
    published_at: Optional[datetime] = None,
) -> NormalizedJob:
    """Валидирует обязательные поля (title, url, external_id или stable hash)."""
    title_n = normalize_text(title)
    url_n = (url or "").strip()
    if not title_n or not url_n.startswith(("http://", "https://")):
        raise SourceSchemaInvalid(f"invalid item: title={title!r} url={url!r}")
    desc_n = normalize_text(description)
    h = stable_hash(url_n, title_n, desc_n)
    ext = (external_id or "").strip() or h
    if budget_min is not None and budget_max is not None and budget_min > budget_max:
        budget_min, budget_max = budget_max, budget_min
    return NormalizedJob(
        external_id=ext[:255],
        url=url_n[:1024],
        title=title_n[:512],
        description=desc_n,
        budget_min=budget_min,
        budget_max=budget_max,
        currency=(currency or "RUB")[:8],
        published_at=published_at,
        raw_hash=h,
    )
