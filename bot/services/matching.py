"""Алгоритм сопоставления (раздел 5 документации).

Быстрый отсев (бюджет, исключения) -> ключевые слова -> опциональная LLM-оценка
-> решение: matched, если score >= 0.70. При ошибке LLM используется score_rules.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from bot.adapters.base import normalize_text
from bot.services.llm_gateway import LLMGateway, LLMUnavailable
from db.models import FilterRule, Job, LLMStatus, Profile, RuleType, Verdict

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.70


@dataclass
class MatchResult:
    verdict: Verdict
    score: float
    reasons: list[str] = field(default_factory=list)
    llm_status: LLMStatus = LLMStatus.skipped

    @property
    def matched(self) -> bool:
        return self.verdict == Verdict.matched


def _job_text(job: Job) -> str:
    return (normalize_text(f"{job.title} {job.description or ''}") or "").lower()


def job_budget(job: Job) -> Optional[int]:
    """Верхняя граница бюджета заказа (или нижняя, если верхней нет)."""
    return job.budget_max if job.budget_max is not None else job.budget_min


def apply_rules(job: Job, profile: Profile, rules: list[FilterRule]) -> MatchResult:
    """Детерминированная часть: бюджет, исключения, ключевые слова."""
    reasons: list[str] = []
    text = _job_text(job)

    budget = job_budget(job)
    if profile.min_budget > 0 and budget is not None:
        if budget < profile.min_budget:
            return MatchResult(Verdict.rejected, 0.0, ["бюджет ниже минимума"])
        budget_ok = True
    else:
        budget_ok = False

    exclusions = [r.value.lower() for r in rules if r.rule_type == RuleType.keyword and r.is_exclusion]
    hit = next((w for w in exclusions if w in text), None)
    if hit:
        return MatchResult(Verdict.rejected, 0.0, [f"исключение: {hit}"])

    keywords = [r.value.lower() for r in rules if r.rule_type == RuleType.keyword and not r.is_exclusion]
    if keywords:
        found = [kw for kw in keywords if kw in text]
        if not found:
            return MatchResult(Verdict.rejected, 0.0, ["нет ключевых слов"])
        reasons.append(", ".join(found[:3]))
        score = min(1.0, 0.7 + 0.1 * (len(found) - 1))
    else:
        score = 0.7  # профиль без ключевых слов: этап пропускается

    if budget_ok:
        reasons.append("бюджет выше минимума")
    return MatchResult(Verdict.matched, round(score, 2), reasons)


async def match_job(job: Job, profile: Profile, rules: list[FilterRule], llm: Optional[LLMGateway]) -> MatchResult:
    rules_result = apply_rules(job, profile, rules)
    if not rules_result.matched:
        return rules_result
    if not (profile.ai_enabled and profile.ai_prompt):
        return rules_result
    if llm is None or not llm.enabled:
        rules_result.llm_status = LLMStatus.unavailable
        return rules_result

    job_card = {
        "title": job.title,
        "description": (job.description or "")[:3000],
        "budget_min": job.budget_min,
        "budget_max": job.budget_max,
        "currency": job.currency,
    }
    try:
        verdict = await llm.evaluate(profile.ai_prompt, job_card)
    except LLMUnavailable as exc:
        # Сценарий C: деградация к правилам, уведомление не блокируется
        logger.warning("LLM unavailable (%s) — используем только правила", exc)
        rules_result.llm_status = LLMStatus.unavailable
        return rules_result

    score = round(verdict.score, 2)
    matched = verdict.match and score >= MATCH_THRESHOLD
    reasons = verdict.reasons or rules_result.reasons
    return MatchResult(Verdict.matched if matched else Verdict.rejected, score, reasons, LLMStatus.ok)
