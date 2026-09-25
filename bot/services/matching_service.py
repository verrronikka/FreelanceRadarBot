from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from db.models import Job, Profile, FilterRule, RuleType


@dataclass
class MatchResult:
    matched: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    llm_status: str = "skipped"


def match_job_to_profile(job: Job, profile: Profile, rules: list[FilterRule]) -> MatchResult:
    reasons: list[str] = []

    # Проверка бюджета
    if job.budget_max is not None and job.budget_max < profile.min_budget:
        return MatchResult(matched=False, reasons=["бюджет ниже минимума"])

    # Ключевые слова и исключения
    keywords = [r.value.lower() for r in rules if r.rule_type == RuleType.keyword and not r.is_exclusion]
    exclusions = [r.value.lower() for r in rules if r.rule_type == RuleType.keyword and r.is_exclusion]

    text = f"{job.title} {job.description or ''}".lower()

    if exclusions and any(kw in text for kw in exclusions):
        return MatchResult(matched=False, reasons=["исключающее слово"])

    if keywords:
        matched_keywords = [kw for kw in keywords if kw in text]
        if not matched_keywords:
            return MatchResult(matched=False, reasons=["нет ключевых слов"])
        reasons.append("ключевые слова: " + ", ".join(matched_keywords))

    reasons.append("бюджет подходит")
    score = 0.8 if keywords else 0.5
    return MatchResult(matched=True, score=score, reasons=reasons, llm_status="skipped")
