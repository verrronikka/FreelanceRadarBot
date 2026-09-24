from __future__ import annotations

from dataclasses import dataclass

from db.models import Job, Profile, FilterRule, RuleType, Verdict, LLMStatus


@dataclass
class MatchResult:
    verdict: Verdict
    score: float
    reasons: list[str]
    llm_status: LLMStatus


def match_job_to_profile(job: Job, profile: Profile, rules: list[FilterRule]) -> MatchResult:
    reasons: list[str] = []

    # Бюджет
    if job.budget_min is not None and profile.min_budget > 0:
        if job.budget_min < profile.min_budget:
            return MatchResult(
                verdict=Verdict.rejected,
                score=0.0,
                reasons=["бюджет ниже минимума"],
                llm_status=LLMStatus.skipped,
            )
        reasons.append("бюджет соответствует")

    # Ключевые слова
    keywords = [r.value for r in rules if r.rule_type == RuleType.keyword and not r.is_exclusion]
    if keywords:
        text = f"{job.title} {job.description or ''}".lower()
        matched = [kw for kw in keywords if kw.lower() in text]
        if not matched:
            return MatchResult(
                verdict=Verdict.rejected,
                score=0.0,
                reasons=["нет ключевых слов"],
                llm_status=LLMStatus.skipped,
            )
        reasons.append(f"ключевые слова: {', '.join(matched)}")

    score = 0.7 if reasons else 0.5
    return MatchResult(
        verdict=Verdict.matched,
        score=score,
        reasons=reasons,
        llm_status=LLMStatus.skipped,
    )
