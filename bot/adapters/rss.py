"""Адаптер для RSS/Atom-ленты заказов + проверка ленты при добавлении из бота.

Документация допускает только официальный API/RSS источника, чьи правила
разрешают автоматическое чтение. Поэтому при добавлении ленты бот:
  * принимает только http(s)-адреса во внешней сети (не localhost/локальная сеть);
  * проверяет robots.txt площадки и отказывает, если чтение ленты запрещено;
  * убеждается, что по адресу действительно RSS/Atom с записями.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlsplit

import httpx

from bot.adapters.robots import USER_AGENT, robots_allows
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
    normalize_text,
)

_ATOM = "{http://www.w3.org/2005/Atom}"

# «Бюджет: 15 000 руб», «5000 ₽», «до 20 000 р.» → число
_BUDGET_RE = re.compile(r"(\d[\d\s  ]{2,}\d|\d{3,})\s*(?:₽|руб|rub|р\.)", re.I)


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_budget(*texts: Optional[str]) -> Optional[int]:
    for text in texts:
        if not text:
            continue
        m = _BUDGET_RE.search(normalize_text(text) or "")
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if digits and 0 < int(digits) <= 100_000_000:
                return int(digits)
    return None


@dataclass
class ParsedFeed:
    title: str
    items: list[NormalizedJob]


def parse_feed(content: bytes) -> ParsedFeed:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise SourceSchemaInvalid(f"bad XML: {exc}") from exc

    feed_title = (
        root.findtext("channel/title") or root.findtext(f"{_ATOM}title") or ""
    ).strip()
    items: list[NormalizedJob] = []
    found_entries = False
    for node in root.iter():
        if node.tag == "item":  # RSS 2.0
            found_entries = True
            title = node.findtext("title") or ""
            link = node.findtext("link") or ""
            desc = node.findtext("description")
            guid = node.findtext("guid")
            published = _parse_date(node.findtext("pubDate"))
        elif node.tag == f"{_ATOM}entry":  # Atom
            found_entries = True
            title = node.findtext(f"{_ATOM}title") or ""
            link_el = node.find(f"{_ATOM}link")
            link = link_el.get("href", "") if link_el is not None else ""
            desc = node.findtext(f"{_ATOM}summary") or node.findtext(f"{_ATOM}content")
            guid = node.findtext(f"{_ATOM}id")
            published = _parse_date(node.findtext(f"{_ATOM}published") or node.findtext(f"{_ATOM}updated"))
        else:
            continue
        try:
            items.append(
                make_job(
                    external_id=guid,
                    url=link.strip(),
                    title=title,
                    description=desc,
                    budget_min=_parse_budget(title, desc),
                    published_at=published,
                )
            )
        except SourceError:
            continue  # одна битая запись не ломает всю ленту
    if not found_entries and root.tag not in ("rss", f"{_ATOM}feed") and root.find("channel") is None:
        raise SourceSchemaInvalid("это не RSS/Atom")
    return ParsedFeed(title=feed_title, items=items)


# ------------------------------------------------------------------ проверка при добавлении


class FeedCheckError(Exception):
    """Ошибка с текстом для пользователя."""


async def _ensure_public_host(host: str) -> None:
    if not host or host.lower() in ("localhost",) or host.lower().endswith((".local", ".localhost", ".internal")):
        raise FeedCheckError("Адреса локальной сети добавлять нельзя.")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FeedCheckError("Не удалось найти такой сайт — проверьте адрес.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FeedCheckError("Адреса локальной сети добавлять нельзя.")


async def check_feed(url: str) -> ParsedFeed:
    """Проверяет ленту перед добавлением. Бросает FeedCheckError с понятным текстом."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise FeedCheckError("Нужна ссылка вида https://сайт/rss")
    await _ensure_public_host(parts.hostname or "")

    async with httpx.AsyncClient(
        timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        # robots.txt: уважаем запрет площадки на автоматическое чтение
        if not await robots_allows(client, url):
            raise FeedCheckError(
                "Площадка запрещает автоматическое чтение этой ленты (robots.txt). "
                "По правилам проекта такой источник подключать нельзя."
            )

        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise FeedCheckError("Сайт не отвечает. Попробуйте позже или проверьте адрес.") from exc
    if resp.status_code in (401, 403):
        raise FeedCheckError("Сайт закрыл доступ к ленте (ошибка доступа).")
    if resp.status_code >= 400:
        raise FeedCheckError(f"Сайт вернул ошибку {resp.status_code}.")
    try:
        feed = parse_feed(resp.content)
    except SourceSchemaInvalid as exc:
        raise FeedCheckError(
            "По ссылке не RSS-лента. Нужна именно ссылка на RSS/Atom (обычно заканчивается на /rss, .xml или feed)."
        ) from exc
    if not feed.items:
        raise FeedCheckError("Лента пустая — в ней нет ни одного заказа.")
    return feed


# ------------------------------------------------------------------ адаптер


class RssAdapter(BaseAdapter):
    def __init__(self, code: str, name: str, feed_url: str, token: str = "") -> None:
        self.source_code = code
        self.source_name = name
        self.feed_url = feed_url
        headers = {"User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_new(self, cursor: Optional[str]) -> FetchResult:
        try:
            resp = await self._client.get(self.feed_url)
        except httpx.HTTPError as exc:
            raise SourceTemporaryFailure(f"{type(exc).__name__}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise SourceAccessDenied(f"HTTP {resp.status_code}")
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            raise SourceRateLimited("HTTP 429", retry_after=float(ra) if ra and ra.isdigit() else None)
        if resp.status_code >= 500:
            raise SourceTemporaryFailure(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise SourceSchemaInvalid(f"HTTP {resp.status_code}")

        feed = parse_feed(resp.content)
        border = datetime.fromisoformat(cursor) if cursor else None
        items = [i for i in feed.items if not (border and i.published_at and i.published_at <= border)]
        dates = [i.published_at for i in feed.items if i.published_at]
        newest = max(dates + ([border] if border else []), default=None)
        return FetchResult(
            source_code=self.source_code,
            cursor=cursor,
            items=items,
            next_cursor=newest.isoformat() if newest else cursor,
        )
