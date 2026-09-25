from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.adapters.types import BaseAdapter, FetchResult
from bot.adapters.example import ExampleAdapter
from db.repository import (
    get_active_sources,
    upsert_job,
    get_profiles_for_source,
    get_filter_rules,
    create_match_decision,
    create_notification_delivery,
)
from db.models import Verdict, LLMStatus
from bot.services.matching_service import match_job_to_profile

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        poll_interval: int = 60,
    ) -> None:
        self.session_maker = session_maker
        self.poll_interval = poll_interval
        self.adapters: dict[str, BaseAdapter] = {
            "example": ExampleAdapter(),
        }
        self.cursors: dict[str, Optional[str]] = {}

    async def poll_once(self) -> None:
        async with self.session_maker() as session:
            sources = await get_active_sources(session)
            for source in sources:
                adapter = self.adapters.get(source.code)
                if adapter is None:
                    logger.warning("No adapter for source %s", source.code)
                    continue

                cursor = self.cursors.get(source.code)
                try:
                    result: FetchResult = await adapter.fetch_new(cursor)
                except Exception as e:
                    logger.exception("Error fetching from %s: %s", source.code, e)
                    continue

                self.cursors[source.code] = result.next_cursor

                for item in result.items:
                    job = await upsert_job(
                        session,
                        source_id=source.id,
                        external_id=item["external_id"],
                        url=item["url"],
                        title=item["title"],
                        description=item.get("description"),
                        budget_min=item.get("budget_min"),
                        budget_max=item.get("budget_max"),
                        currency=item.get("currency", "RUB"),
                        published_at=item.get("published_at"),
                    )

                    profiles = await get_profiles_for_source(session, source.id)
                    for profile in profiles:
                        rules = await get_filter_rules(session, profile.id)
                        match = match_job_to_profile(job, profile, rules)
                        if match.matched:
                            decision = await create_match_decision(
                                session,
                                profile_id=profile.id,
                                job_id=job.id,
                                verdict=Verdict.matched,
                                score=match.score,
                                reasons="; ".join(match.reasons) if match.reasons else None,
                                llm_status=LLMStatus.skipped,
                            )
                            await create_notification_delivery(session, decision.id)

            await session.commit()
        logger.info("Poll cycle completed")

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception as e:
                logger.exception("Poll cycle error: %s", e)
            await asyncio.sleep(self.poll_interval)
