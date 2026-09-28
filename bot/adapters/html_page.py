"""Источник «страница со списком заказов» — для сайтов без RSS.

Бот скачивает страницу (как обычный браузер, без обхода защит) и находит на ней
повторяющиеся ссылки на заказы: у списка заказов ссылки однотипные
(/project/123, /jobs/456-title …), поэтому берётся самая многочисленная группа
однотипных ссылок. Меню, шапка и подвал пропускаются. Бюджет ищется в тексте
рядом со ссылкой.

Если сайт отвечает 401/403 (закрыл доступ ботам), источник не подключается —
обход антибот-защит исключён документацией проекта.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlsplit

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
    normalize_text,
)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FreelanceRadarBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.8",
}

_BLOCKS = {"div", "li", "article", "tr", "section", "td", "p"}
_SKIP = {"nav", "header", "footer", "script", "style", "noscript", "form", "aside"}
_JUNK = re.compile(
    r"(login|logout|signin|signup|register|auth|password|help|faq|about|contact|privacy|terms|"
    r"rules|policy|blog|news|tag|search|profile|user|freelancers?|portfolio|cart|pricing|"
    r"page=|sort=|lang=|javascript:|mailto:|tel:)",
    re.I,
)
_BUDGET_RE = re.compile(
    r"(\d[\d\s  ]{2,}\d|\d{3,})\s*(?:₽|руб|rub|р\.|грн|uah|₴|\$|usd|€|eur)", re.I
)


@dataclass
class _Anchor:
    href: str
    text: str = ""
    context: Optional[str] = None


@dataclass
class _Block:
    text: list[str] = field(default_factory=list)
    anchors: list[_Anchor] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.feed_links: list[str] = []
        self.anchors: list[_Anchor] = []
        self._blocks: list[tuple[str, _Block]] = []
        self._skip_depth = 0
        self._in_title = False
        self._anchor: Optional[_Anchor] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "link" and "alternate" in a.get("rel", "").lower():
            if any(t in a.get("type", "").lower() for t in ("rss", "atom")) and a.get("href"):
                self.feed_links.append(a["href"])
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip_depth += 1
        if tag in _BLOCKS:
            self._blocks.append((tag, _Block()))
        if tag == "a" and self._skip_depth == 0 and a.get("href"):
            self._anchor = _Anchor(href=a["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "a" and self._anchor is not None:
            self._anchor.text = " ".join(self._anchor.text.split())
            self.anchors.append(self._anchor)
            if self._blocks:
                self._blocks[-1][1].anchors.append(self._anchor)
            self._anchor = None
        if tag in _BLOCKS:
            # закрываем ближайший блок этого типа; его текст — контекст для ссылок внутри
            for i in range(len(self._blocks) - 1, -1, -1):
                if self._blocks[i][0] == tag:
                    _, block = self._blocks.pop(i)
                    text = " ".join(" ".join(block.text).split())[:600]
                    for anc in block.anchors:
                        if anc.context is None:
                            anc.context = text
                    if self._blocks:  # текст и ссылки поднимаются в родительский блок
                        self._blocks[-1][1].text.extend(block.text)
                        self._blocks[-1][1].anchors.extend(block.anchors)
                    break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skip_depth:
            return
        if self._anchor is not None:
            self._anchor.text += data
        if self._blocks:
            self._blocks[-1][1].text.append(data)


def _pattern(path: str) -> str:
    """/project/12345-landing.html -> 'project/#'  — «форма» ссылки для группировки."""
    segs = [s for s in path.split("/") if s]
    shape = [re.sub(r"\d+", "#", s) if re.search(r"\d", s) else s for s in segs[:-1]]
    last = segs[-1] if segs else ""
    shape.append("#" if re.search(r"\d", last) else ("*" if len(segs) > 1 else last))
    return "/".join(shape)


def _budget(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = _BUDGET_RE.search(text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits and 0 < int(digits) <= 100_000_000 else None


@dataclass
class ParsedPage:
    title: str
    feed_urls: list[str]
    items: list[NormalizedJob]


def parse_page(html: str, page_url: str) -> ParsedPage:
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # битый HTML
        raise SourceSchemaInvalid(f"bad HTML: {exc}") from exc

    host = (urlsplit(page_url).hostname or "").removeprefix("www.")
    page_path = urlsplit(page_url).path.rstrip("/")
    groups: dict[str, list[_Anchor]] = defaultdict(list)
    seen: set[str] = set()
    for anc in parser.anchors:
        url = urljoin(page_url, anc.href).split("#", 1)[0]
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        if (parts.hostname or "").removeprefix("www.") != host:
            continue
        if not parts.path or parts.path.rstrip("/") in ("", page_path) or _JUNK.search(url):
            continue
        if not 10 <= len(anc.text) <= 200 or url in seen:
            continue
        seen.add(url)
        anc.href = url
        groups[_pattern(parts.path)].append(anc)

    items: list[NormalizedJob] = []
    if groups:
        best = max(groups.values(), key=len)
        if len(best) >= 3:
            for anc in best[:60]:
                ctx = normalize_text(anc.context) or ""
                desc = ctx.replace(anc.text, "", 1).strip(" .,-—|•") or None
                try:
                    items.append(
                        make_job(
                            external_id=anc.href,
                            url=anc.href,
                            title=anc.text,
                            description=desc[:500] if desc else None,
                            budget_min=_budget(ctx),
                        )
                    )
                except SourceError:
                    continue
    return ParsedPage(
        title=" ".join(parser.title.split()),
        feed_urls=[urljoin(page_url, u) for u in parser.feed_links],
        items=items,
    )


class HtmlPageAdapter(BaseAdapter):
    def __init__(self, code: str, name: str, page_url: str) -> None:
        self.source_code = code
        self.source_name = name
        self.page_url = page_url
        self._client = httpx.AsyncClient(timeout=20, headers=BROWSER_HEADERS, follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_new(self, cursor: Optional[str]) -> FetchResult:
        try:
            resp = await self._client.get(self.page_url)
        except httpx.HTTPError as exc:
            raise SourceTemporaryFailure(f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise SourceAccessDenied(f"HTTP {resp.status_code}")
        if resp.status_code == 429:
            raise SourceRateLimited("HTTP 429")
        if resp.status_code >= 500:
            raise SourceTemporaryFailure(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise SourceSchemaInvalid(f"HTTP {resp.status_code}")
        page = parse_page(resp.text, str(resp.url))
        if not page.items:
            raise SourceSchemaInvalid("на странице не найден список заказов")
        # Новизну определяет upsert по ссылке заказа: уже виденные не придут повторно
        return FetchResult(source_code=self.source_code, cursor=cursor, items=page.items, next_cursor="page")
