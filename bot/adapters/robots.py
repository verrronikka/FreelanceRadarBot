"""Проверка robots.txt по стандарту RFC 9309 (как у Google/Яндекса).

Стандартный urllib.robotparser не понимает шаблоны `*` и `$` в путях
(например, `Disallow: */rss/*` на FL.ru) и из-за этого разрешает то,
что площадка запретила. Здесь — полноценное сопоставление:
  * группа правил выбирается по самому точному User-agent, иначе `*`;
  * среди подходящих правил побеждает самое длинное; при равенстве — Allow.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

USER_AGENT = "FreelanceRadarBot/1.0"
_UA_TOKEN = "freelanceradarbot"


def _parse(text: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            last_was_agent = False
            if agents and value:  # пустой Disallow ничего не запрещает
                rules.append((key == "allow", value))
        else:
            last_was_agent = False
    if agents:
        groups.append((agents, rules))
    return groups


def _to_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile(rx + ("$" if anchored else ""))


def is_allowed(robots_txt: str, url: str, ua_token: str = _UA_TOKEN) -> bool:
    groups = _parse(robots_txt)
    specific = [g for g in groups if any(a != "*" and a in ua_token for a in g[0])]
    selected = specific or [g for g in groups if "*" in g[0]]
    rules = [r for g in selected for r in g[1]]
    if not rules:
        return True
    parts = urlsplit(url)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    best: tuple[int, bool] | None = None  # (длина шаблона, allow)
    for allow, pattern in rules:
        if _to_regex(pattern).match(target):
            key = (len(pattern), allow)
            if best is None or key > best:  # длиннее — важнее; при равенстве True (Allow) > False
                best = key
    return True if best is None else best[1]


async def robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    """True, если robots.txt сайта разрешает нашему боту читать url (или robots.txt нет)."""
    parts = urlsplit(url)
    try:
        r = await client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    except httpx.HTTPError:
        return True
    if r.status_code != 200:
        return True
    return is_allowed(r.text, url)
