import csv
import secrets
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import (
    AdminAuditLog,
    AttemptResult,
    Giveaway,
    GiveawayStatus,
    MessageAttempt,
    Operator,
    OperatorAlias,
    Participant,
    Winner,
)
from app.normalization import normalize_operator_text

PRIZES = ("500 GB", "300 GB", "200 GB", "100 GB")


def utcnow() -> datetime:
    return datetime.now(UTC)


def next_wednesday_window(timezone: str) -> tuple[datetime, datetime]:
    local_now = datetime.now(ZoneInfo(timezone))
    days_ahead = (2 - local_now.weekday()) % 7  # Wednesday is 2
    start = local_now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    if start <= local_now:
        start += timedelta(days=7)
    return start.astimezone(UTC), start.replace(hour=20).astimezone(UTC)


async def audit(session: AsyncSession, admin_id: int, action: str, giveaway_id: int | None, detail: str = "") -> None:
    session.add(AdminAuditLog(admin_telegram_id=admin_id, action=action, giveaway_id=giveaway_id, detail=detail[:500] or None))
    await session.commit()


async def find_operator(session: AsyncSession, text: str) -> Operator | None:
    normalized = normalize_operator_text(text)
    if not normalized:
        return None
    stmt = (
        select(Operator)
        .join(OperatorAlias, OperatorAlias.operator_id == Operator.id)
        .where(Operator.active.is_(True), OperatorAlias.normalized_alias == normalized)
    )
    return await session.scalar(stmt)


async def record_attempt(
    session: AsyncSession,
    giveaway_id: int | None,
    user_id: int,
    message_id: int,
    result: AttemptResult,
    reason: str = "",
) -> None:
    session.add(
        MessageAttempt(
            giveaway_id=giveaway_id,
            telegram_user_id=user_id,
            message_id=message_id,
            result=result,
            reason=reason[:300] or None,
        )
    )
    await session.commit()


async def is_channel_member(bot: Bot, channel: str, user_id: int) -> tuple[bool, str]:
    member = await bot.get_chat_member(channel, user_id)
    status = member.status
    # Restricted members are included only when Telegram explicitly says they remain members.
    return status in {"creator", "administrator", "member"} or (
        status == "restricted" and bool(getattr(member, "is_member", False))
    ), status


async def close_expired(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.execute(
            update(Giveaway)
            .where(Giveaway.status == GiveawayStatus.ACTIVE, Giveaway.ends_at <= utcnow())
            .values(status=GiveawayStatus.CLOSED, closed_at=utcnow())
        )
        await session.commit()
        return result.rowcount or 0


async def start_due(session_factory: async_sessionmaker[AsyncSession]) -> int:
    now = utcnow()
    async with session_factory() as session:
        result = await session.execute(
            update(Giveaway)
            .where(
                Giveaway.status == GiveawayStatus.IDLE,
                Giveaway.schedule_confirmed.is_(True),
                Giveaway.starts_at <= now,
                Giveaway.ends_at > now,
            )
            .values(status=GiveawayStatus.ACTIVE)
        )
        await session.commit()
        return result.rowcount or 0


async def participant_exists(session: AsyncSession, giveaway_id: int, user_id: int) -> bool:
    return (await session.scalar(select(Participant.id).where(Participant.giveaway_id == giveaway_id, Participant.telegram_user_id == user_id))) is not None


async def register_participant(
    session: AsyncSession,
    giveaway: Giveaway,
    user_id: int,
    username: str | None,
    first_name: str,
    message_text: str,
    message_id: int,
    sent_at: datetime,
    operator: Operator,
    membership_status: str,
) -> bool:
    session.add(
        Participant(
            giveaway_id=giveaway.id,
            telegram_user_id=user_id,
            username=username,
            first_name=first_name[:256],
            message_text=message_text,
            message_id=message_id,
            chat_id=giveaway.comment_chat_id,
            message_sent_at=sent_at,
            operator_id=operator.id,
            operator_raw=message_text[:500],
            membership_at_entry=True,
            membership_last_checked_at=utcnow(),
            membership_last_status=membership_status,
            eligible=True,
        )
    )
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def stats(session: AsyncSession, giveaway_id: int) -> tuple[int, int, int]:
    attempts = await session.scalar(select(func.count(MessageAttempt.id)).where(MessageAttempt.giveaway_id == giveaway_id)) or 0
    registered = await session.scalar(select(func.count(Participant.id)).where(Participant.giveaway_id == giveaway_id)) or 0
    eligible = await session.scalar(select(func.count(Participant.id)).where(Participant.giveaway_id == giveaway_id, Participant.eligible.is_(True))) or 0
    return attempts, registered, eligible


async def recheck_membership(bot: Bot, session: AsyncSession, settings: Settings, giveaway_id: int) -> tuple[int, int]:
    rows = (await session.scalars(select(Participant).where(Participant.giveaway_id == giveaway_id))).all()
    eligible_count = 0
    for participant in rows:
        try:
            is_member, status = await is_channel_member(bot, settings.channel_username, participant.telegram_user_id)
        except Exception:
            # Do not disqualify users because Telegram is temporarily unavailable.
            continue
        participant.membership_last_checked_at = utcnow()
        participant.membership_last_status = status
        participant.eligible = is_member
        participant.ineligibility_reason = None if is_member else "not a channel member at final check"
        eligible_count += int(is_member)
    await session.commit()
    return len(rows), eligible_count


async def draw_winners(session: AsyncSession, giveaway_id: int) -> list[Winner]:
    giveaway = await session.get(Giveaway, giveaway_id)
    if giveaway is None or giveaway.status != GiveawayStatus.CLOSED:
        raise ValueError("Giveaway must be CLOSED before drawing winners.")
    if await session.scalar(select(Winner.id).where(Winner.giveaway_id == giveaway_id).limit(1)):
        raise ValueError("Winners have already been selected for this giveaway.")
    participants = (await session.scalars(select(Participant).where(Participant.giveaway_id == giveaway_id, Participant.eligible.is_(True)))).all()
    if len(participants) < len(PRIZES):
        raise ValueError("Fewer than four eligible participants are available.")
    chosen = secrets.SystemRandom().sample(participants, k=len(PRIZES))
    winners = [Winner(giveaway_id=giveaway_id, participant_id=p.id, rank=index, prize=PRIZES[index - 1]) for index, p in enumerate(chosen, 1)]
    session.add_all(winners)
    await session.commit()
    return winners


async def export_bytes(session: AsyncSession, giveaway_id: int, kind: str) -> tuple[bytes, str, str]:
    if kind == "ids":
        ids = (await session.scalars(select(Participant.telegram_user_id).where(Participant.giveaway_id == giveaway_id, Participant.eligible.is_(True)).order_by(Participant.telegram_user_id))).all()
        return ("\n".join(map(str, ids)) + ("\n" if ids else "")).encode(), f"giveaway-{giveaway_id}-eligible-ids.txt", "text/plain"
    query: Select[tuple[Participant]] = select(Participant).where(Participant.giveaway_id == giveaway_id)
    if kind == "eligible":
        query = query.where(Participant.eligible.is_(True))
    if kind not in {"eligible", "participants"}:
        raise ValueError("Export type must be ids, eligible, or participants.")
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["telegram_user_id", "username", "first_name", "operator", "message_id", "sent_at", "eligible", "membership_status"])
    for p in (await session.scalars(query.order_by(Participant.registered_at))).all():
        writer.writerow([p.telegram_user_id, p.username or "", p.first_name, p.operator_raw, p.message_id, p.message_sent_at.isoformat(), p.eligible, p.membership_last_status or ""])
    return output.getvalue().encode("utf-8-sig"), f"giveaway-{giveaway_id}-{kind}.csv", "text/csv"
