"""Контрактные тесты адаптеров источников (план, задача 4): каждый адаптер отдаёт
FetchResult с нормализованными заказами — title, url, external_id обязательны."""
import asyncio

import httpx
import pytest

import bot.adapters.discovery as discovery
from bot.adapters.base import FetchResult, SourceSchemaInvalid, make_job, normalize_text
from bot.adapters.example import ExampleAdapter
from bot.adapters.freelancehunt import _parse_project
from bot.adapters.html_page import parse_page
from bot.adapters.rss import FeedCheckError, _parse_budget, parse_feed

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Биржа</title>
<item><title>3D-модель для игры (Бюджет: 15 000 руб.)</title><link>https://ex.com/p/1</link>
<description>&lt;p&gt;Blender&lt;/p&gt;</description><guid>1</guid><pubDate>Sun, 27 Sep 2026 10:00:00 +0300</pubDate></item>
<item><title>Логотип</title><link>https://ex.com/p/2</link><description>Оплата 5000 ₽</description></item>
<item><title></title><link>bad</link></item>
</channel></rss>""".encode()

HTML = """<html><head><title>Проекты по дизайну — Биржа</title></head><body>
<header><a href="/projects/it">Программирование сайтов</a><a href="/login">Войти в аккаунт</a></header>
<div class="list">
 <article><a href="/project/101-logo.html">Логотип для кофейни в Москве</a><p>Айдентика</p><span>Бюджет: 12 000 руб.</span></article>
 <article><a href="/project/102-banner.html">Баннеры для рекламы ВКонтакте</a><span>3000 ₴</span></article>
 <article><a href="/project/103-3d.html">3D-модель персонажа в Blender</a></article>
</div>
<div><a href="/blog/how-to-win">Как выигрывать тендеры на бирже</a></div>
<footer><a href="/about">О компании и контакты</a></footer></body></html>"""


def test_make_job_requires_title_and_url():
    with pytest.raises(SourceSchemaInvalid):
        make_job(url="not-a-url", title="x")
    with pytest.raises(SourceSchemaInvalid):
        make_job(url="https://ex.com/1", title="   ")


def test_make_job_stable_hash_when_no_external_id():
    a = make_job(url="https://ex.com/1", title="Заказ")
    b = make_job(url="https://ex.com/1", title="Заказ")
    assert a.external_id == b.external_id  # повторный опрос не создаст дубль


def test_normalize_text_strips_html():
    assert normalize_text("<p>Привет&nbsp;<b>мир</b></p>") == "Привет мир"


def test_rss_feed_parsing():
    feed = parse_feed(RSS)
    assert feed.title == "Биржа"
    assert [i.title for i in feed.items] == ["3D-модель для игры (Бюджет: 15 000 руб.)", "Логотип"]
    assert feed.items[0].budget_min == 15000
    assert feed.items[0].description == "Blender"
    assert feed.items[1].budget_min == 5000


def test_atom_feed_parsing():
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title><entry><title>Job</title>' \
           b'<link href="https://ex.com/a"/><id>x1</id><updated>2026-09-27T10:00:00Z</updated></entry></feed>'
    feed = parse_feed(atom)
    assert feed.items[0].url == "https://ex.com/a"
    assert feed.items[0].external_id == "x1"


def test_not_a_feed():
    with pytest.raises(SourceSchemaInvalid):
        parse_feed(b"<html><body>hi</body></html>")


def test_budget_parsing():
    assert _parse_budget("до 20 000 р. за всё") == 20000
    assert _parse_budget("5 лет опыта") is None


def test_html_page_finds_job_list_and_skips_menu():
    page = parse_page(HTML, "https://birzha.ru/projects/design")
    urls = [i.url for i in page.items]
    assert urls == [
        "https://birzha.ru/project/101-logo.html",
        "https://birzha.ru/project/102-banner.html",
        "https://birzha.ru/project/103-3d.html",
    ]
    assert page.items[0].budget_min == 12000
    assert page.title.startswith("Проекты по дизайну")


def test_freelancehunt_project_parsing():
    p = {
        "id": 123,
        "attributes": {
            "name": "3D модель стула",
            "description_html": "<p>Нужна модель</p>",
            "skills": [{"id": 1, "name": "3D моделирование"}],
            "budget": {"amount": 1500, "currency": "UAH"},
            "published_at": "2026-09-27T12:00:00+03:00",
        },
        "links": {"self": {"web": "https://freelancehunt.com/project/3d/123.html"}},
    }
    job = _parse_project(p)
    assert job.title == "3D модель стула"
    assert job.budget_min == 1500 and job.currency == "UAH"
    assert "3D моделирование" in job.description
    assert _parse_project({"id": 5, "attributes": {"name": "Без бюджета", "budget": None}}).budget_min is None


def test_example_adapter_contract_and_keywords():
    async def keywords():
        return ["вязание"]

    adapter = ExampleAdapter(keyword_provider=keywords)
    res = asyncio.run(adapter.fetch_new(None))
    assert isinstance(res, FetchResult)
    assert res.next_cursor == "2"
    assert len(res.items) == 3
    assert any("вязание" in i.title for i in res.items)  # демо подстраивается под профессию
    for item in res.items:
        assert item.url.startswith("https://") and item.title and item.external_id


# --- проверка ссылки, присланной пользователем (без интернета) ---


def _mock_site(monkeypatch, pages):
    async def public(host):
        return None

    def handler(request):
        if request.url.path not in pages:
            return httpx.Response(404)
        code, ctype, body = pages[request.url.path]
        return httpx.Response(code, headers={"content-type": ctype}, content=body)

    real = httpx.AsyncClient
    monkeypatch.setattr(discovery, "_ensure_public_host", public)
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_check_source_page(monkeypatch):
    _mock_site(monkeypatch, {"/projects/design": (200, "text/html", HTML.encode())})
    res = asyncio.run(discovery.check_source("https://birzha.ru/projects/design"))
    assert res.kind == "page" and len(res.items) == 3


def test_check_source_discovers_rss(monkeypatch):
    head = b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'
    _mock_site(monkeypatch, {"/x": (200, "text/html", head), "/feed.xml": (200, "application/rss+xml", RSS)})
    res = asyncio.run(discovery.check_source("https://s.com/x"))
    assert res.kind == "rss" and res.url == "https://s.com/feed.xml"


def test_check_source_respects_robots(monkeypatch):
    _mock_site(monkeypatch, {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /x"),
        "/x": (200, "text/html", HTML.encode()),
    })
    with pytest.raises(FeedCheckError, match="robots"):
        asyncio.run(discovery.check_source("https://s.com/x"))


def test_check_source_blocked_site(monkeypatch):
    _mock_site(monkeypatch, {"/x": (403, "text/html", b"no")})
    with pytest.raises(FeedCheckError, match="403"):
        asyncio.run(discovery.check_source("https://s.com/x"))


@pytest.mark.parametrize("url", ["ftp://x", "http://localhost/rss", "http://192.168.1.5/rss", "http://127.0.0.1:8080/"])
def test_check_source_rejects_local_and_bad_urls(url):
    with pytest.raises(FeedCheckError):
        asyncio.run(discovery.check_source(url))
