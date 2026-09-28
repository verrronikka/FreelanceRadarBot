"""Repository-слой: все обращения к БД собраны здесь."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import delete, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    DeliveryStatus,
    FilterRule,
    Job,
    LLMStatus,
    MatchDecision,
    NotificationDelivery,
    Profile,
    ProfileSource,
    RuleType,
    Source,
    SourceStatus,
    User,
    UserStatus,
    Verdict,
)

# ---------------------------------------------------------------- sources


async def get_active_sources(session: AsyncSession) -> list[Source]:
    result = await session.execute(
        select(Source).where(Source.status == SourceStatus.active).order_by(Source.id)
    )
    return list(result.scalars().all())


async def ensure_source(session: AsyncSession, code: str, name: str, poll_interval_sec: int) -> Source:
    """Создаёт источник, если его ещё нет (seed при старте)."""
    result = await session.execute(select(Source).where(Source.code == code))
    source = result.scalar_one_or_none()
    if source is None:
        source = Source(code=code, name=name, status=SourceStatus.active, poll_interval_sec=max(30, poll_interval_sec))
        session.add(source)
        await session.flush()
    return source


async def pause_source(session: AsyncSession, source_id: int) -> None:
    await session.execute(update(Source).where(Source.id == source_id).values(status=SourceStatus.paused))


# ---------------------------------------------------------------- users & profiles


async def get_or_create_user(session: AsyncSession, telegram_user_id: int) -> User:
    result = await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_user_id=telegram_user_id, status=UserStatus.active)
        session.add(user)
        await session.flush()
    elif user.status == UserStatus.deleted:
        # Повторный /start после удаления — пользователь снова активен
        user.status = UserStatus.active
    return user


async def get_user(session: AsyncSession, telegram_user_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
    return result.scalar_one_or_none()


async def get_user_profile(session: AsyncSession, user_id: UUID) -> Optional[Profile]:
    """В MVP у пользователя один профиль (модель данных допускает несколько)."""
    result = await session.execute(
        select(Profile)
        .where(Profile.user_id == user_id)
        .options(selectinload(Profile.sources), selectinload(Profile.filter_rules))
        .order_by(Profile.created_at)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def save_profile(
    session: AsyncSession,
    user: User,
    name: str,
    source_ids: Sequence[int],
    min_budget: int,
    keywords: Sequence[str],
    exclusions: Sequence[str],
    ai_prompt: Optional[str],
    ai_enabled: bool,
) -> Profile:
    """Создаёт или обновляет профиль вместе с источниками и правилами (одна транзакция)."""
    # Без eager-загрузки коллекций: ниже они заменяются целиком через SQL
    result = await session.execute(
        select(Profile).where(Profile.user_id == user.id).order_by(Profile.created_at).limit(1)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = Profile(user_id=user.id, name=name, min_budget=min_budget, version=1)
        session.add(profile)
        await session.flush()
    else:
        profile.version += 1
    profile.name = name
    profile.min_budget = min_budget
    profile.ai_prompt = ai_prompt
    profile.ai_enabled = ai_enabled

    await session.execute(delete(ProfileSource).where(ProfileSource.profile_id == profile.id))
    await session.execute(delete(FilterRule).where(FilterRule.profile_id == profile.id))
    for sid in dict.fromkeys(source_ids):
        session.add(ProfileSource(profile_id=profile.id, source_id=sid))
    for kw in dict.fromkeys(keywords):
        session.add(FilterRule(profile_id=profile.id, rule_type=RuleType.keyword, value=kw, is_exclusion=False))
    for kw in dict.fromkeys(exclusions):
        if kw in keywords:
            continue
        session.add(FilterRule(profile_id=profile.id, rule_type=RuleType.keyword, value=kw, is_exclusion=True))
    await session.flush()
    return profile


async def add_exclusion(session: AsyncSession, profile_id: UUID, value: str) -> bool:
    stmt = (
        pg_insert(FilterRule)
        .values(profile_id=profile_id, rule_type=RuleType.keyword, value=value, is_exclusion=True)
        .on_conflict_do_update(constraint="uq_filter_rule", set_={"is_exclusion": True})
    )
    await session.execute(stmt)
    await session.execute(update(Profile).where(Profile.id == profile_id).values(version=Profile.version + 1))
    return True


async def delete_user_data(session: AsyncSession, user: User) -> None:
    """Удаление профиля: чистим персональные настройки и историю, помечаем пользователя deleted."""
    await session.execute(delete(Profile).where(Profile.user_id == user.id))  # каскадом — правила, решения, доставки
    user.status = UserStatus.deleted
    user.notifications_paused = False


# ---------------------------------------------------------------- jobs & matching


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
) -> tuple[Job, bool]:
    """Идемпотентный upsert по (source_id, external_id). Возвращает (job, is_new)."""
    values = dict(
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
    # xmax = 0 у только что вставленной строки -> так узнаём, новая ли она
    stmt = (
        pg_insert(Job)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_job_external",
            set_={k: v for k, v in values.items() if k not in ("source_id", "external_id")} | {"updated_at": datetime.now(timezone.utc)},
        )
        .returning(Job.id, literal_column("(xmax = 0)").label("inserted"))
    )
    row = (await session.execute(stmt)).one()
    job = await session.get(Job, row.id, populate_existing=True)
    assert job is not None
    return job, bool(row.inserted)


async def get_profiles_for_source(session: AsyncSession, source_id: int) -> list[Profile]:
    """Профили активных пользователей, подписанных на источник."""
    result = await session.execute(
        select(Profile)
        .join(ProfileSource, ProfileSource.profile_id == Profile.id)
        .join(User, User.id == Profile.user_id)
        .where(ProfileSource.source_id == source_id, User.status == UserStatus.active)
        .options(selectinload(Profile.filter_rules), selectinload(Profile.user))
    )
    return list(result.scalars().unique().all())


async def get_decision(session: AsyncSession, profile_id: UUID, job_id: UUID) -> Optional[MatchDecision]:
    result = await session.execute(
        select(MatchDecision).where(MatchDecision.profile_id == profile_id, MatchDecision.job_id == job_id)
    )
    return result.scalar_one_or_none()


async def create_match_decision(
    session: AsyncSession,
    profile_id: UUID,
    job_id: UUID,
    verdict: Verdict,
    score: Optional[float],
    reasons: Optional[str],
    llm_status: LLMStatus,
) -> MatchDecision:
    existing = await get_decision(session, profile_id, job_id)
    if existing is not None:
        return existing
    decision = MatchDecision(
        profile_id=profile_id, job_id=job_id, verdict=verdict, score=score, reasons=reasons, llm_status=llm_status
    )
    session.add(decision)
    await session.flush()
    return decision


async def create_notification_delivery(session: AsyncSession, decision_id: UUID) -> NotificationDelivery:
    stmt = (
        pg_insert(NotificationDelivery)
        .values(decision_id=decision_id, status=DeliveryStatus.pending, attempts=0)
        .on_conflict_do_nothing(index_elements=["decision_id"])
    )
    await session.execute(stmt)
    result = await session.execute(select(NotificationDelivery).where(NotificationDelivery.decision_id == decision_id))
    return result.scalar_one()


async def get_pending_deliveries(session: AsyncSession, limit: int = 50) -> list[NotificationDelivery]:
    result = await session.execute(
        select(NotificationDelivery)
        .where(NotificationDelivery.status == DeliveryStatus.pending)
        .options(
            selectinload(NotificationDelivery.decision).selectinload(MatchDecision.job).selectinload(Job.source),
            selectinload(NotificationDelivery.decision).selectinload(MatchDecision.profile).selectinload(Profile.user),
        )
        .order_by(NotificationDelivery.created_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_recent_matches(session: AsyncSession, profile_id: UUID, limit: int = 5) -> list[MatchDecision]:
    result = await session.execute(
        select(MatchDecision)
        .where(MatchDecision.profile_id == profile_id, MatchDecision.verdict == Verdict.matched)
        .options(selectinload(MatchDecision.job).selectinload(Job.source))
        .order_by(MatchDecision.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_job(session: AsyncSession, job_id: UUID) -> Optional[Job]:
    return await session.get(Job, job_id)


async def purge_old_job_texts(session: AsyncSession, days: int) -> int:
    """Приватность: текст заказа храним максимум N дней, затем удаляем."""
    border = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        update(Job).where(Job.created_at < border, Job.description.is_not(None)).values(description=None)
    )
    return result.rowcount or 0


async def get_decision_stats(session: AsyncSession, profile_id: UUID, limit: int = 200) -> dict[str, int]:
    """Почему заказы не подошли: причина -> количество (по последним решениям)."""
    result = await session.execute(
        select(MatchDecision.verdict, MatchDecision.reasons)
        .where(MatchDecision.profile_id == profile_id)
        .order_by(MatchDecision.created_at.desc())
        .limit(limit)
    )
    stats: dict[str, int] = {}
    for verdict, reasons in result.all():
        key = "подошли" if verdict == Verdict.matched else (reasons or "не подошли")
        stats[key] = stats.get(key, 0) + 1
    return stats


async def get_active_keywords(session: AsyncSession, source_code: str) -> list[str]:
    """Ключевые слова активных пользователей, подписанных на источник (для демо-источника)."""
    result = await session.execute(
        select(FilterRule.value)
        .join(Profile, Profile.id == FilterRule.profile_id)
        .join(User, User.id == Profile.user_id)
        .join(ProfileSource, ProfileSource.profile_id == Profile.id)
        .join(Source, Source.id == ProfileSource.source_id)
        .where(
            FilterRule.rule_type == RuleType.keyword,
            FilterRule.is_exclusion.is_(False),
            User.status == UserStatus.active,
            Source.code == source_code,
        )
        .distinct()
        .order_by(FilterRule.value)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------- источники, добавленные пользователями


async def get_source_by_feed_url(session: AsyncSession, feed_url: str) -> Optional[Source]:
    result = await session.execute(select(Source).where(Source.feed_url == feed_url))
    return result.scalar_one_or_none()


async def create_feed_source(
    session: AsyncSession, feed_url: str, name: str, added_by: int, poll_interval_sec: int, kind: str = "rss"
) -> Source:
    """Создаёт RSS-источник (или возвращает существующий с тем же адресом и снимает его с паузы)."""
    import hashlib

    source = await get_source_by_feed_url(session, feed_url)
    if source is not None:
        source.status = SourceStatus.active
        source.feed_kind = kind
        return source
    code = ("rss_" if kind == "rss" else "web_") + hashlib.sha1(feed_url.encode("utf-8")).hexdigest()[:12]
    source = Source(
        code=code,
        name=name[:120],
        status=SourceStatus.active,
        poll_interval_sec=max(60, poll_interval_sec),  # к чужим сайтам — не чаще раза в минуту
        feed_url=feed_url,
        added_by=added_by,
        feed_kind=kind,
    )
    session.add(source)
    await session.flush()
    return source


async def subscribe_profile(session: AsyncSession, profile_id: UUID, source_id: int) -> None:
    stmt = (
        pg_insert(ProfileSource)
        .values(profile_id=profile_id, source_id=source_id)
        .on_conflict_do_nothing()
    )
    await session.execute(stmt)
    await session.execute(update(Profile).where(Profile.id == profile_id).values(version=Profile.version + 1))


async def unsubscribe_profile(session: AsyncSession, profile_id: UUID, source_id: int) -> None:
    await session.execute(
        delete(ProfileSource).where(ProfileSource.profile_id == profile_id, ProfileSource.source_id == source_id)
    )
    await session.execute(update(Profile).where(Profile.id == profile_id).values(version=Profile.version + 1))
