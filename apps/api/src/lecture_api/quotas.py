"""What each signed-in user may spend, and everyone together.

Counted per UTC day from the usage ledger (`usage_events`), which records each lecture added,
question asked and LLM bill as it happens: a user's questions and lectures, and the day's LLM
spend, from each answer's tokens (priced by lecture_llm.pricing) and each processing run's
recorded cost (written by the worker). Deleting a lecture or a conversation leaves the ledger
alone, so it gives nothing back. A limit answers 429 with Retry-After. The checks are soft: two
requests at the same moment can both pass. Admins and the local user have no limits
(docs/adr/0009-clerk-sign-in-and-quotas.md).
"""

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lecture_api.auth import Viewer
from lecture_core.models import UsageEvent, UsageKind
from lecture_core.settings import Settings
from lecture_llm.pricing import text_cost_usd


class QuotaUsage(BaseModel):
    """A signed-in user's limits and what they've used today."""

    questions_today: int
    questions_per_day: int
    questions_per_minute: int
    uploads_today: int
    uploads_per_day: int
    upload_bytes: int
    # Everyone's LLM spend reached today's ceiling: questions and processing wait until reset.
    paused: bool
    resets_at: datetime


async def check_question(
    session: AsyncSession, viewer: Viewer, settings: Settings, now: datetime | None = None
) -> None:
    if viewer.user is None or not viewer.limited:
        return
    now = now or datetime.now(UTC)
    asked = await _questions_since(session, viewer.user.id, now - timedelta(minutes=1))
    if asked >= settings.quota_questions_per_minute:
        raise _limit(
            f"That's {settings.quota_questions_per_minute} questions in a minute, the most "
            "allowed. Wait a moment.",
            retry_after_s=60,
        )
    asked = await _questions_since(session, viewer.user.id, _day_start(now))
    if asked >= settings.quota_questions_per_day:
        raise _limit(
            f"You've asked {asked} questions today, the most a day. More at 00:00 UTC.",
            retry_after_s=_until_tomorrow(now),
        )
    await _check_budget(session, settings, now)


async def check_upload(
    session: AsyncSession, viewer: Viewer, settings: Settings, now: datetime | None = None
) -> None:
    if viewer.user is None or not viewer.limited:
        return
    now = now or datetime.now(UTC)
    uploaded = await _uploads_since(session, viewer.user.id, _day_start(now))
    if uploaded >= settings.quota_uploads_per_day:
        raise _limit(
            f"You've added {uploaded} lectures today, the most a day. More at 00:00 UTC.",
            retry_after_s=_until_tomorrow(now),
        )
    await _check_budget(session, settings, now)


async def check_processing(
    session: AsyncSession, viewer: Viewer, settings: Settings, now: datetime | None = None
) -> None:
    if viewer.limited:
        await _check_budget(session, settings, now or datetime.now(UTC))


def upload_limit(viewer: Viewer, settings: Settings) -> int:
    """The largest file the viewer may upload."""
    if viewer.limited:
        return min(settings.quota_upload_bytes, settings.max_upload_bytes)
    return settings.max_upload_bytes


async def usage(
    session: AsyncSession, viewer: Viewer, settings: Settings, now: datetime | None = None
) -> QuotaUsage | None:
    if viewer.user is None or not viewer.limited:
        return None
    now = now or datetime.now(UTC)
    today = _day_start(now)
    return QuotaUsage(
        questions_today=await _questions_since(session, viewer.user.id, today),
        questions_per_day=settings.quota_questions_per_day,
        questions_per_minute=settings.quota_questions_per_minute,
        uploads_today=await _uploads_since(session, viewer.user.id, today),
        uploads_per_day=settings.quota_uploads_per_day,
        upload_bytes=upload_limit(viewer, settings),
        paused=await spend_since(session, today) >= settings.daily_llm_budget_usd,
        resets_at=today + timedelta(days=1),
    )


def record(
    session: AsyncSession, kind: UsageKind, user_id: uuid.UUID | None, cost_usd: float = 0.0
) -> None:
    """Add to the ledger, in the caller's transaction."""
    session.add(UsageEvent(user_id=user_id, kind=kind, cost_usd=cost_usd))


def answer_cost(model: str | None, used: dict[str, Any] | None) -> float:
    """What an answer's LLM calls cost, at paid-tier prices."""
    if not model or not used:
        return 0.0
    cost = text_cost_usd(model, used.get("input_tokens", 0), used.get("output_tokens", 0))
    return cost or 0.0


async def spend_since(session: AsyncSession, since: datetime) -> float:
    """The LLM spend of every answer and processing run since `since`."""
    spent = await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
            UsageEvent.kind == UsageKind.LLM, UsageEvent.created_at >= since
        )
    )
    return float(spent or 0.0)


async def _check_budget(session: AsyncSession, settings: Settings, now: datetime) -> None:
    if await spend_since(session, _day_start(now)) >= settings.daily_llm_budget_usd:
        raise _limit(
            "Today's budget for the language model is spent. Questions and processing resume "
            "at 00:00 UTC.",
            retry_after_s=_until_tomorrow(now),
        )


async def _questions_since(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> int:
    return await _count_since(session, UsageKind.QUESTION, user_id, since)


async def _uploads_since(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> int:
    return await _count_since(session, UsageKind.UPLOAD, user_id, since)


async def _count_since(
    session: AsyncSession, kind: UsageKind, user_id: uuid.UUID, since: datetime
) -> int:
    count = await session.scalar(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.kind == kind, UsageEvent.user_id == user_id, UsageEvent.created_at >= since
        )
    )
    return count or 0


def _day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _until_tomorrow(now: datetime) -> int:
    return math.ceil((_day_start(now) + timedelta(days=1) - now).total_seconds())


def _limit(detail: str, retry_after_s: int) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, detail, headers={"Retry-After": str(retry_after_s)}
    )
