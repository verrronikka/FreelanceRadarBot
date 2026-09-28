"""Правила фильтрации (сценарий B, раздел 5): matched / rejected и деградация при недоступном LLM."""
import asyncio
from types import SimpleNamespace

from bot.services.llm_gateway import LLMUnavailable, LLMVerdict
from bot.services.matching import apply_rules, match_job
from db.models import LLMStatus, RuleType, Verdict


def job(title="Монтаж ролика в After Effects", desc="Промо 60 сек", bmin=25000, bmax=35000):
    return SimpleNamespace(title=title, description=desc, budget_min=bmin, budget_max=bmax, currency="RUB")


def profile(min_budget=0, ai=False, prompt=None):
    return SimpleNamespace(min_budget=min_budget, ai_enabled=ai, ai_prompt=prompt)


def kw(value, exclusion=False):
    return SimpleNamespace(rule_type=RuleType.keyword, value=value, is_exclusion=exclusion)


def test_keyword_match():
    r = apply_rules(job(), profile(10000), [kw("after effects")])
    assert r.verdict == Verdict.matched
    assert r.score >= 0.7
    assert "after effects" in r.reasons[0] and "бюджет выше минимума" in r.reasons


def test_no_keywords_rejected():
    r = apply_rules(job(), profile(), [kw("blender")])
    assert r.verdict == Verdict.rejected and r.reasons == ["нет ключевых слов"]


def test_budget_below_minimum_rejected():
    r = apply_rules(job(bmin=5000, bmax=8000), profile(10000), [kw("монтаж")])
    assert r.verdict == Verdict.rejected and r.reasons == ["бюджет ниже минимума"]


def test_exclusion_rejected():
    r = apply_rules(job(title="Монтаж свадебного видео"), profile(), [kw("монтаж"), kw("свадеб", True)])
    assert r.verdict == Verdict.rejected and r.reasons[0].startswith("исключение")


def test_profile_without_keywords_matches_everything():
    r = apply_rules(job(), profile(), [])
    assert r.verdict == Verdict.matched


def test_job_without_budget_is_not_cut_by_budget():
    r = apply_rules(job(bmin=None, bmax=None), profile(50000), [kw("монтаж")])
    assert r.verdict == Verdict.matched


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.enabled = True

    async def evaluate(self, profile_text, job_card):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_llm_ok_uses_llm_score():
    llm = FakeLLM(LLMVerdict(match=True, score=0.91, reasons=["длинное видео"]))
    r = asyncio.run(match_job(job(), profile(ai=True, prompt="монтаж"), [kw("монтаж")], llm))
    assert r.verdict == Verdict.matched and r.score == 0.91 and r.llm_status == LLMStatus.ok


def test_llm_says_no():
    llm = FakeLLM(LLMVerdict(match=False, score=0.2, reasons=["не по теме"]))
    r = asyncio.run(match_job(job(), profile(ai=True, prompt="монтаж"), [kw("монтаж")], llm))
    assert r.verdict == Verdict.rejected and r.llm_status == LLMStatus.ok


def test_llm_unavailable_falls_back_to_rules():
    """Сценарий C: ошибка/таймаут LLM не блокирует уведомление."""
    llm = FakeLLM(LLMUnavailable("timeout"))
    r = asyncio.run(match_job(job(), profile(ai=True, prompt="монтаж"), [kw("монтаж")], llm))
    assert r.verdict == Verdict.matched and r.llm_status == LLMStatus.unavailable


def test_llm_not_called_for_rejected_candidate():
    llm = FakeLLM(AssertionError("LLM не должен вызываться"))
    r = asyncio.run(match_job(job(), profile(ai=True, prompt="x"), [kw("blender")], llm))
    assert r.verdict == Verdict.rejected
