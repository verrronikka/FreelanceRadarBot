"""Проверка ссылки, которую пользователь прислал как новый источник.

Порядок:
  1. ссылка сама RSS/Atom → источник «rss»;
  2. HTML-страница со ссылкой на RSS в <head> → источник «rss» с найденной лентой;
  3. HTML-страница со списком заказов → источник «page» (разбор страницы).
На каждом шаге уважаем robots.txt; сайты, закрывшие доступ ботам (401/403),
не подключаем.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from bot.adapters.base import NormalizedJob, SourceSchemaInvalid
from bot.adapters.html_page import BROWSER_HEADERS, parse_page
from bot.adapters.rss import FeedCheckError, USER_AGENT, _ensure_public_host, parse_feed


@dataclass
class SourceCheck:
    kind: Literal["rss", "page"]
    url: str
    title: str
    items: list[NormalizedJob]


async def _robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    parts = urlsplit(url)
    try:
        r = await client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    except httpx.HTTPError:
        return True
    if r.status_code != 200:
        return True
    rp = RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp.can_fetch(USER_AGENT, url)


def _looks_like_feed(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "").lower()
    head = resp.content[:500].lstrip().lower()
    return "xml" in ctype or "rss" in ctype or "atom" in ctype or head.startswith(b"<?xml") or b"<rss" in head


async def check_source(url: str) -> SourceCheck:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise FeedCheckError("Нужна ссылка вида https://сайт.ru/…")
    await _ensure_public_host(parts.hostname or "")

    async with httpx.AsyncClient(timeout=20, headers=BROWSER_HEADERS, follow_redirects=True) as client:
        if not await _robots_allows(client, url):
            raise FeedCheckError(
                "Сайт запрещает автоматическое чтение этой страницы (robots.txt), поэтому подключить её нельзя. "
                "Поищите у площадки RSS-ленту или официальный API."
            )
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise FeedCheckError("Сайт не отвечает. Попробуйте позже или проверьте адрес.") from exc

        if resp.status_code in (401, 403):
            raise FeedCheckError(
                "Сайт закрыл доступ для ботов (ошибка 403). Обходить такую защиту нельзя — "
                "нужен официальный API или RSS площадки."
            )
        if resp.status_code >= 400:
            raise FeedCheckError(f"Сайт вернул ошибку {resp.status_code}.")
        final_url = str(resp.url)

        # 1. сама ссылка — лента
        if _looks_like_feed(resp):
            try:
                feed = parse_feed(resp.content)
            except SourceSchemaInvalid:
                feed = None
            if feed and feed.items:
                return SourceCheck("rss", final_url, feed.title or parts.hostname or "RSS", feed.items)

        page = parse_page(resp.text, final_url)

        # 2. на странице объявлена RSS-лента
        for feed_url in page.feed_urls[:3]:
            if not await _robots_allows(client, feed_url):
                continue
            try:
                fr = await client.get(feed_url)
                feed = parse_feed(fr.content) if fr.status_code == 200 else None
            except (httpx.HTTPError, SourceSchemaInvalid):
                feed = None
            if feed and feed.items:
                return SourceCheck("rss", feed_url, feed.title or page.title or parts.hostname or "RSS", feed.items)

        # 3. разбор самой страницы
        if len(page.items) >= 3:
            title = page.title.split(" — ")[0].split(" | ")[0].strip() or parts.hostname or "Сайт"
            return SourceCheck("page", final_url, title[:100], page.items)

    raise FeedCheckError(
        "Не нашла на странице списка заказов. Пришлите ссылку прямо на страницу со списком заказов "
        "(например, категорию), а не на главную. Если сайт строит список скриптом, нужен его RSS или API."
    )
