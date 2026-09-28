"""Повторы: временная ошибка источника ретраится, 400/403 — нет; Telegram 429/5xx — повтор доставки."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter

import bot.scheduler as scheduler_mod
import bot.services.delivery as delivery
from bot.adapters.base import (
    BaseAdapter,
    FetchResult,
    SourceRateLimited,
    SourceSchemaInvalid,
    SourceTemporaryFailure,
)
from bot.scheduler import Scheduler
from db.models import DeliveryStatus, LLMStatus, UserStatus


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(_):
        return None

    monkeypatch.setattr(scheduler_mod.asyncio, "sleep", fast)


class FlakyAdapter(BaseAdapter):
    source_code = "flaky"
    source_name = "Flaky"

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    async def fetch_new(self, cursor):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return FetchResult(source_code="flaky", cursor=cursor, items=[], next_cursor="1")


def run_fetch(adapter):
    s = Scheduler(bot=None, session_maker=None, adapters={})
    return asyncio.run(s._fetch_with_retry(adapter, None))


def test_temporary_failure_is_retried():
    a = FlakyAdapter([SourceTemporaryFailure("5xx"), SourceTemporaryFailure("timeout")])
    assert run_fetch(a).next_cursor == "1"
    assert a.calls == 3


def test_temporary_failure_gives_up_after_3_retries():
    a = FlakyAdapter([SourceTemporaryFailure()] * 10)
    with pytest.raises(SourceTemporaryFailure):
        run_fetch(a)
    assert a.calls == 4  # первая попытка + 3 повтора


@pytest.mark.parametrize("err", [SourceSchemaInvalid("400"), SourceRateLimited("429")])
def test_non_retryable_errors(err):
    a = FlakyAdapter([err])
    with pytest.raises(type(err)):
        run_fetch(a)
    assert a.calls == 1


# --- доставка в Telegram ---


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        return None


class FakeBot:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sent = []

    async def send_message(self, **kw):
        out = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(out, Exception):
            raise out
        self.sent.append(kw)


def make_delivery():
    user = SimpleNamespace(status=UserStatus.active, notifications_paused=False, telegram_user_id=42)
    job = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001", url="https://ex.com/1", title="Монтаж",
        description=None, budget_min=1000, budget_max=None, currency="RUB",
        published_at=datetime.now(timezone.utc), source=SimpleNamespace(name="Demo"),
    )
    decision = SimpleNamespace(job=job, profile=SimpleNamespace(user=user), reasons="монтаж", llm_status=LLMStatus.skipped)
    return SimpleNamespace(id="d1", decision=decision, status=DeliveryStatus.pending, attempts=0, error_code=None, sent_at=None)


def run_delivery(monkeypatch, d, bot):
    async def pending(session, limit=50):
        return [d] if d.status == DeliveryStatus.pending else []

    monkeypatch.setattr(delivery, "get_pending_deliveries", pending)
    return asyncio.run(delivery.send_pending(bot, lambda: FakeSession()))


def test_delivery_success(monkeypatch):
    d = make_delivery()
    bot = FakeBot([None])
    assert run_delivery(monkeypatch, d, bot) == 1
    assert d.status == DeliveryStatus.sent and d.sent_at is not None
    assert "Монтаж" in bot.sent[0]["text"]


def test_telegram_429_is_postponed_not_counted(monkeypatch):
    d = make_delivery()
    run_delivery(monkeypatch, d, FakeBot([TelegramRetryAfter(method=None, message="flood", retry_after=0)]))
    assert d.status == DeliveryStatus.pending and d.attempts == 0 and d.error_code == "TELEGRAM_RATE_LIMITED"
    run_delivery(monkeypatch, d, FakeBot([None]))  # следующий тик — успешно
    assert d.status == DeliveryStatus.sent


def test_temporary_telegram_error_retried_then_failed(monkeypatch):
    d = make_delivery()
    for _ in range(delivery.MAX_ATTEMPTS):
        run_delivery(monkeypatch, d, FakeBot([TelegramNetworkError(method=None, message="timeout")]))
    assert d.status == DeliveryStatus.failed and d.attempts == delivery.MAX_ATTEMPTS


def test_blocked_bot_marks_user(monkeypatch):
    d = make_delivery()
    run_delivery(monkeypatch, d, FakeBot([TelegramForbiddenError(method=None, message="blocked")]))
    assert d.status == DeliveryStatus.failed
    assert d.decision.profile.user.status == UserStatus.blocked


def test_paused_user_gets_nothing(monkeypatch):
    d = make_delivery()
    d.decision.profile.user.notifications_paused = True
    bot = FakeBot([None])
    run_delivery(monkeypatch, d, bot)
    assert d.status == DeliveryStatus.cancelled and bot.sent == []
