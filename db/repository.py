from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Job,
    Source,
    SourceStatus,
    Profile,
    ProfileSource,
    FilterRule,
    MatchDecision,
    NotificationDelivery,
    Verdict,
    LLMStatus,
    DeliveryStatus,
)


async def get_active_sources(session: AsyncSession) -> list[Source]:
    result = await session.execute(
        select(Source).where(Source.status == SourceStatus.active)
    )
    return list(result.scalars().all())


async def upsert_job(
    session: AsyncSession,
    source_id: int,
    external_id: str,
    url: str,
    title: str,
    description: Optional[str],
    budget_min: Optional[int],
    budget_max: Optional[int],
    currency: str,
    published_at: Optional[datetime],
    raw_hash: Optional[str] = None,
) -> Job:
    result = await session.execute(
        select(Job).where(Job.source_id == source_id, Job.external_id == external_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        job = Job(
            source_id=source_id,
            external_id=external_id,
            url=url,
            title=title,
            description=description,
            budget_min=budget_min,
            budget_max=budget_max,
            currency=currency,
            published_at=published_at,
            raw_hash=raw_hash,
        )
        session.add(job)
        await session.flush()
    else:
        job.url = url
        job.title = title
        job.description = description
        job.budget_min = budget_min
        job.budget_max = budget_max
        job.currency = currency
        job.published_at = published_at
        job.raw_hash = raw_hash
        await session.flush()
    return job


async def get_profiles_for_source(session: AsyncSession, source_id: int) -> list[Profile]:
    result = await session.execute(
        select(Profile)
        .join(ProfileSource, ProfileSource.profile_id == Profile.id)
        .where(ProfileSource.source_id == source_id)
    )
    return list(result.scalars().all())


async def get_filter_rules(session: AsyncSession, profile_id: UUID) -> list[FilterRule]:
    result = await session.execute(
        select(FilterRule).where(FilterRule.profile_id == profile_id)
    )
    return list(result.scalars().all())


async def create_match_decision(
    session: AsyncSession,
    profile_id: UUID,
    job_id: UUID,
    verdict: Verdict,
    score: Optional[float],
    reasons: Optional[str],
    llm_status: LLMStatus,
) -> MatchDecision:
    existing = await session.execute(
        select(MatchDecision).where(
            MatchDecision.profile_id == profile_id,
            MatchDecision.job_id == job_id,
        )
    )
    decision = existing.scalar_one_or_none()
    if decision is not None:
        return decision

    decision = MatchDecision(
        profile_id=profile_id,
        job_id=job_id,
        verdict=verdict,
        score=score,
        reasons=reasons,
        llm_status=llm_status,
    )
    session.add(decision)
    await session.flush()
    return decision


async def create_notification_delivery(
    session: AsyncSession,
    decision_id: UUID,
) -> NotificationDelivery:
    existing = await session.execute(
        select(NotificationDelivery).where(NotificationDelivery.decision_id == decision_id)
    )
    delivery = existing.scalar_one_or_none()
    if delivery is not None:
        return delivery

    delivery = NotificationDelivery(
        decision_id=decision_id,
        status=DeliveryStatus.pending,
    )
    session.add(delivery)
    await session.flush()
    return delivery
