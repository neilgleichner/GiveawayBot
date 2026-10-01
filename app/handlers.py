from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import structlog

from app.config import Settings
from app.limiter import RateLimiter
from app.models import AttemptResult, Giveaway, GiveawayStatus, Operator, OperatorAlias, Participant
from app.normalization import normalize_operator_text
from app.services import (
    audit,
    close_expired,
    draw_winners,
    export_bytes,
    find_operator,
    is_channel_member,
    participant_exists,
    record_attempt,
    recheck_membership,
    register_participant,
    stats,
    utcnow,
)

log = structlog.get_logger()


def _is_admin(message: Message, settings: Settings) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_telegram_ids)


def _parts(message: Message) -> list[str]:
    return (message.text or "").split(maxsplit=3)


def build_router(settings: Settings, session_factory: async_sessionmaker[AsyncSession], limiter: RateLimiter) -> Router:
    router = Router(name="giveaway")

    @router.message(Command("start", "help"), F.chat.type == "private")
    async def start_help(message: Message) -> None:
        if _is_admin(message, settings):
            await message.answer(
                "ربات Giveaway آسان‌پنل فعال است.\n\n"
                "برای تست سریع:\n"
                "1. در کامنت پست: /whereami سپس /new_giveaway تست\n"
                "2. در همین چت خصوصی: /start_giveaway <id>\n\n"
                "برای زمان‌بندی: /schedule_giveaway <id> YYYY-MM-DD HH:MM YYYY-MM-DD HH:MM\n"
                "و سپس: /confirm_schedule <id>"
            )
        else:
            await message.answer("این ربات فقط برای مدیریت قرعه‌کشی آسان‌پنل استفاده می‌شود.")

    @router.message(Command("whoami"))
    async def whoami(message: Message) -> None:
        """Safe diagnostic: reveals only the caller's own Telegram and current chat IDs."""
        if message.from_user is None:
            return
        admin_status = "بله" if _is_admin(message, settings) else "خیر"
        await message.reply(
            f"Your Telegram User ID: {message.from_user.id}\n"
            f"Current Chat ID: {message.chat.id}\n"
            f"Is configured admin: {admin_status}"
        )

    @router.message(Command("whereami"))
    async def whereami(message: Message) -> None:
        """Private diagnostic for binding the configured discussion group and its post thread."""
        if not _is_admin(message, settings):
            return
        await message.reply(
            f"Chat ID: {message.chat.id}\n"
            f"Thread ID: {message.message_thread_id or 'ندارد (داخل کامنت یک پست کانال اجرا کنید)'}"
        )

    @router.message(F.chat.id == settings.comment_chat_id, F.text, ~F.text.startswith("/"), ~F.via_bot)
    async def participant_message(message: Message, bot: Bot) -> None:
        if message.from_user is None or message.from_user.is_bot or not message.text:
            return
        try:
            if not await limiter.allowed(f"message:{message.from_user.id}", 12, 60):
                return
        except Exception:
            log.warning("rate_limiter_unavailable", user_id=message.from_user.id)
            return

        await close_expired(session_factory)
        async with session_factory() as session:
            giveaway = await session.scalar(
                select(Giveaway)
                .where(
                    Giveaway.comment_chat_id == message.chat.id,
                    Giveaway.message_thread_id == message.message_thread_id,
                )
                .order_by(Giveaway.id.desc())
                .limit(1)
            )
            # Messages in all other discussion threads are intentionally ignored.
            if giveaway is None:
                return
            if giveaway.status != GiveawayStatus.ACTIVE:
                if giveaway.status == GiveawayStatus.CLOSED:
                    await message.reply("⏰ زمان شرکت در این قرعه‌کشی به پایان رسیده است.")
                return
            if giveaway.ends_at and giveaway.ends_at <= utcnow():
                await close_expired(session_factory)
                await message.reply("⏰ زمان شرکت در این قرعه‌کشی به پایان رسیده است.")
                return
            if await participant_exists(session, giveaway.id, message.from_user.id):
                await record_attempt(session, giveaway.id, message.from_user.id, message.message_id, AttemptResult.DUPLICATE)
                await message.reply("⚠️ شما قبلاً در این قرعه‌کشی شرکت کرده‌اید.")
                return
            operator = await find_operator(session, message.text)
            if operator is None:
                await record_attempt(session, giveaway.id, message.from_user.id, message.message_id, AttemptResult.INVALID_OPERATOR)
                await message.reply("لطفاً فقط اسم اپراتور اینترنت خودتون رو ارسال کنید.\nمثال: همراه اول، ایرانسل، شاتل، آسیاتک و…")
                return
            try:
                member, membership_status = await is_channel_member(bot, settings.channel_username, message.from_user.id)
            except Exception:
                log.exception("membership_check_failed", giveaway_id=giveaway.id, user_id=message.from_user.id)
                await record_attempt(session, giveaway.id, message.from_user.id, message.message_id, AttemptResult.SYSTEM_ERROR, "membership check unavailable")
                await message.reply("یک خطای موقت رخ داد؛ لطفاً دوباره تلاش کنید.")
                return
            if not member:
                await record_attempt(session, giveaway.id, message.from_user.id, message.message_id, AttemptResult.NOT_MEMBER, membership_status)
                await message.reply("⚠️ برای شرکت در قرعه‌کشی ابتدا باید عضو کانال آسان‌پنل شوید.\n\n🔗 @AsanPanel_News\n\nبعد از عضویت، دوباره پیام خود را ارسال کنید.")
                return
            accepted = await register_participant(
                session=session,
                giveaway=giveaway,
                user_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                message_text=message.text,
                message_id=message.message_id,
                sent_at=message.date,
                operator=operator,
                membership_status=membership_status,
            )
            if not accepted:
                await record_attempt(session, giveaway.id, message.from_user.id, message.message_id, AttemptResult.DUPLICATE, "unique constraint")
                await message.reply("⚠️ شما قبلاً در این قرعه‌کشی شرکت کرده‌اید.")
                return
            await record_attempt(session, giveaway.id, message.from_user.id, message.message_id, AttemptResult.ACCEPTED)
            await message.reply("✅ ثبت شد.\nشما با موفقیت در قرعه‌کشی شرکت کردید.")

    @router.message(Command("new_giveaway"), F.chat.id == settings.comment_chat_id)
    async def new_giveaway(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        if message.message_thread_id is None:
            await message.reply("این دستور باید داخل thread کامنت همان پست کانال ارسال شود.")
            return
        title = " ".join(_parts(message)[1:]).strip() or "AsanPanel Giveaway"
        async with session_factory() as session:
            existing = await session.scalar(select(Giveaway.id).where(Giveaway.comment_chat_id == message.chat.id, Giveaway.message_thread_id == message.message_thread_id, Giveaway.status.in_([GiveawayStatus.IDLE, GiveawayStatus.ACTIVE])))
            if existing:
                await message.reply(f"برای این thread یک Giveaway باز با شناسه {existing} وجود دارد.")
                return
            giveaway = Giveaway(
                title=title[:200],
                comment_chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                schedule_confirmed=False,
            )
            session.add(giveaway)
            await session.commit()
            await session.refresh(giveaway)
            await audit(session, message.from_user.id, "create_giveaway", giveaway.id, f"thread={message.message_thread_id}")
        await message.reply(
            f"✅ Giveaway #{giveaway.id} به‌صورت پیش‌نویس ساخته و به همین کامنت قفل شد.\n"
            "هیچ زمان‌بندی هنوز فعال نیست. در چت خصوصی Bot ابتدا زمان را با "
            f"/schedule_giveaway {giveaway.id} 2026-10-07 18:00 2026-10-07 20:00 تعیین کنید، "
            f"سپس با /confirm_schedule {giveaway.id} تأیید کنید."
        )

    @router.message(Command("schedule_giveaway"), F.chat.type == "private")
    async def schedule_giveaway(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        tokens = (message.text or "").split()
        if len(tokens) != 6 or not tokens[1].isdigit():
            await message.answer("استفاده: /schedule_giveaway <id> YYYY-MM-DD HH:MM YYYY-MM-DD HH:MM\nزمان‌ها به وقت تهران هستند.")
            return
        try:
            timezone = ZoneInfo(settings.timezone)
            starts_at = datetime.strptime(f"{tokens[2]} {tokens[3]}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone).astimezone(UTC)
            ends_at = datetime.strptime(f"{tokens[4]} {tokens[5]}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone).astimezone(UTC)
        except ValueError:
            await message.answer("فرمت تاریخ یا ساعت صحیح نیست.")
            return
        if starts_at <= utcnow() or ends_at <= starts_at:
            await message.answer("زمان شروع باید در آینده و زمان پایان بعد از شروع باشد.")
            return
        async with session_factory() as session:
            giveaway = await session.get(Giveaway, int(tokens[1]))
            if giveaway is None or giveaway.status != GiveawayStatus.IDLE:
                await message.answer("فقط Giveaway پیش‌نویس (IDLE) قابل زمان‌بندی است.")
                return
            giveaway.starts_at = starts_at
            giveaway.ends_at = ends_at
            giveaway.schedule_confirmed = False
            await session.commit()
            await audit(session, message.from_user.id, "set_schedule", giveaway.id, f"{starts_at.isoformat()} to {ends_at.isoformat()}")
        await message.answer("✅ زمان ذخیره شد، اما هنوز فعال نیست. جزئیات را بررسی و سپس /confirm_schedule <id> را اجرا کنید.")

    @router.message(Command("confirm_schedule"), F.chat.type == "private")
    async def confirm_schedule(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /confirm_schedule <id>")
            return
        async with session_factory() as session:
            giveaway = await session.get(Giveaway, int(parts[1]))
            if giveaway is None or giveaway.status != GiveawayStatus.IDLE or not giveaway.starts_at or not giveaway.ends_at:
                await message.answer("ابتدا یک Giveaway پیش‌نویس با زمان شروع و پایان معتبر بسازید.")
                return
            if giveaway.starts_at <= utcnow() or giveaway.ends_at <= giveaway.starts_at:
                await message.answer("بازه زمان دیگر معتبر نیست؛ دوباره زمان‌بندی کنید.")
                return
            giveaway.schedule_confirmed = True
            await session.commit()
            await audit(session, message.from_user.id, "confirm_schedule", giveaway.id)
        await message.answer("✅ زمان‌بندی تأیید شد. Bot فقط در زمان تعیین‌شده آن را فعال و در پایان آن را می‌بندد.")

    @router.message(Command("start_giveaway"), F.chat.type == "private")
    async def start_giveaway(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /start_giveaway <id>")
            return
        async with session_factory() as session:
            giveaway = await session.get(Giveaway, int(parts[1]))
            if giveaway is None or giveaway.status == GiveawayStatus.CLOSED:
                await message.answer("Giveaway یافت نشد یا بسته شده است.")
                return
            giveaway.status = GiveawayStatus.ACTIVE
            giveaway.starts_at = utcnow()
            giveaway.ends_at = utcnow() + timedelta(hours=2)
            giveaway.schedule_confirmed = False
            await session.commit()
            await audit(session, message.from_user.id, "manual_start", giveaway.id)
        await message.answer("✅ Giveaway فعال شد و دو ساعت دیگر به‌صورت خودکار بسته می‌شود.")

    @router.message(Command("close_giveaway", "stop_giveaway"), F.chat.type == "private")
    async def close_giveaway(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /close_giveaway <id>")
            return
        async with session_factory() as session:
            giveaway = await session.get(Giveaway, int(parts[1]))
            if giveaway is None:
                await message.answer("Giveaway یافت نشد.")
                return
            giveaway.status = GiveawayStatus.CLOSED
            giveaway.closed_at = utcnow()
            await session.commit()
            await audit(session, message.from_user.id, "manual_close", giveaway.id)
        await message.answer("✅ Giveaway بسته شد.")

    @router.message(Command("reset_giveaway"), F.chat.type == "private")
    async def reset_giveaway(message: Message) -> None:
        """Create a fresh scheduled giveaway without deleting the historical record."""
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /reset_giveaway <id>")
            return
        async with session_factory() as session:
            previous = await session.get(Giveaway, int(parts[1]))
            if previous is None or previous.status != GiveawayStatus.CLOSED:
                await message.answer("فقط Giveaway بسته‌شده قابل Reset است؛ برای حفظ سابقه، داده‌های قبلی حذف نمی‌شوند.")
                return
            replacement_exists = await session.scalar(
                select(Giveaway.id).where(
                    Giveaway.comment_chat_id == previous.comment_chat_id,
                    Giveaway.message_thread_id == previous.message_thread_id,
                    Giveaway.status.in_([GiveawayStatus.IDLE, GiveawayStatus.ACTIVE]),
                )
            )
            if replacement_exists:
                await message.answer(f"برای این thread Giveaway باز #{replacement_exists} وجود دارد.")
                return
            replacement = Giveaway(
                title=previous.title,
                comment_chat_id=previous.comment_chat_id,
                message_thread_id=previous.message_thread_id,
                schedule_confirmed=False,
            )
            session.add(replacement)
            await session.commit()
            await session.refresh(replacement)
            await audit(session, message.from_user.id, "reset_giveaway", replacement.id, f"from={previous.id}")
        await message.answer(f"✅ Giveaway جدید #{replacement.id} ساخته شد؛ داده‌های Giveaway قبلی حذف نشدند.")

    @router.message(Command("stats"), F.chat.type == "private")
    async def giveaway_stats(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /stats <id>")
            return
        async with session_factory() as session:
            totals = await stats(session, int(parts[1]))
        await message.answer(f"Total Attempts: {totals[0]}\nRegistered Participants: {totals[1]}\nEligible Participants: {totals[2]}")

    @router.message(Command("status"), F.chat.type == "private")
    async def giveaway_status(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /status <id>")
            return
        async with session_factory() as session:
            giveaway = await session.get(Giveaway, int(parts[1]))
        if giveaway is None:
            await message.answer("Giveaway یافت نشد.")
            return
        await message.answer(
            f"#{giveaway.id}\nStatus: {giveaway.status.value}\n"
            f"Thread: {giveaway.message_thread_id}\n"
            f"Start: {giveaway.starts_at}\nEnd: {giveaway.ends_at}"
        )

    @router.message(Command("remove_eligibility"), F.chat.type == "private")
    async def remove_eligibility(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
            await message.answer("استفاده: /remove_eligibility <giveaway_id> <user_id> <reason>")
            return
        reason = parts[3] if len(parts) > 3 else "removed by admin"
        async with session_factory() as session:
            participant = await session.scalar(
                select(Participant).where(
                    Participant.giveaway_id == int(parts[1]), Participant.telegram_user_id == int(parts[2])
                )
            )
            if participant is None:
                await message.answer("شرکت‌کننده یافت نشد.")
                return
            participant.eligible = False
            participant.ineligibility_reason = reason[:300]
            await session.commit()
            await audit(session, message.from_user.id, "remove_eligibility", int(parts[1]), f"user={parts[2]}; {reason}")
        await message.answer("✅ کاربر از فهرست واجدان شرایط حذف شد؛ سابقه ثبت حفظ شده است.")

    @router.message(Command("export"), F.chat.type == "private")
    async def export(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 3 or not parts[1].isdigit():
            await message.answer("استفاده: /export <id> ids|eligible|participants")
            return
        async with session_factory() as session:
            try:
                content, filename, _ = await export_bytes(session, int(parts[1]), parts[2].lower())
                await audit(session, message.from_user.id, "export", int(parts[1]), parts[2].lower())
            except ValueError as exc:
                await message.answer(str(exc))
                return
        await message.answer_document(BufferedInputFile(content, filename=filename), caption="خروجی خصوصی Giveaway")

    @router.message(Command("recheck_membership"), F.chat.type == "private")
    async def check_membership(message: Message, bot: Bot) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /recheck_membership <id>")
            return
        async with session_factory() as session:
            checked, eligible = await recheck_membership(bot, session, settings, int(parts[1]))
            await audit(session, message.from_user.id, "recheck_membership", int(parts[1]))
        await message.answer(f"بررسی شد: {checked}\nواجد شرایط: {eligible}")

    @router.message(Command("draw"), F.chat.type == "private")
    async def draw(message: Message, bot: Bot) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /draw <id>")
            return
        giveaway_id = int(parts[1])
        async with session_factory() as session:
            await recheck_membership(bot, session, settings, giveaway_id)
            try:
                winners = await draw_winners(session, giveaway_id)
            except ValueError as exc:
                await message.answer(str(exc))
                return
            rows = []
            for winner in winners:
                participant = await session.get(Participant, winner.participant_id)
                rows.append(f"{winner.rank}. User ID: {participant.telegram_user_id} — {winner.prize}")
            await audit(session, message.from_user.id, "draw", giveaway_id)
        await message.answer("🏆 نتایج قرعه‌کشی داخلی:\n" + "\n".join(rows))

    @router.message(Command("history"), F.chat.type == "private")
    async def history(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        async with session_factory() as session:
            rows = (await session.scalars(select(Giveaway).order_by(Giveaway.id.desc()).limit(15))).all()
        await message.answer("\n".join(f"#{g.id} | {g.status.value} | {g.title}" for g in rows) or "هنوز Giveawayی وجود ندارد.")

    @router.message(Command("operator_add"), F.chat.type == "private")
    async def operator_add(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        name = " ".join(_parts(message)[1:]).strip()
        if not name:
            await message.answer("استفاده: /operator_add <نام استاندارد>")
            return
        async with session_factory() as session:
            if await session.scalar(select(Operator.id).where(Operator.canonical_name == name)):
                await message.answer("این اپراتور از قبل وجود دارد.")
                return
            operator = Operator(canonical_name=name[:100])
            session.add(operator)
            await session.flush()
            session.add(OperatorAlias(operator_id=operator.id, alias=name[:150], normalized_alias=normalize_operator_text(name)))
            await session.commit()
            await audit(session, message.from_user.id, "operator_add", None, name)
        await message.answer(f"✅ اپراتور ثبت شد: #{operator.id}")

    @router.message(Command("operator_alias_add"), F.chat.type == "private")
    async def alias_add(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 3 or not parts[1].isdigit():
            await message.answer("استفاده: /operator_alias_add <operator_id> <alias>")
            return
        alias = " ".join(parts[2:]).strip()
        async with session_factory() as session:
            if await session.get(Operator, int(parts[1])) is None:
                await message.answer("اپراتور یافت نشد.")
                return
            normalized = normalize_operator_text(alias)
            if await session.scalar(select(OperatorAlias.id).where(OperatorAlias.normalized_alias == normalized)):
                await message.answer("این Alias قبلاً برای اپراتوری ثبت شده است.")
                return
            session.add(OperatorAlias(operator_id=int(parts[1]), alias=alias[:150], normalized_alias=normalized))
            await session.commit()
            await audit(session, message.from_user.id, "operator_alias_add", None, alias)
        await message.answer("✅ Alias ثبت شد.")

    @router.message(Command("operators"), F.chat.type == "private")
    async def operators(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        async with session_factory() as session:
            rows = (await session.scalars(select(Operator).order_by(Operator.id))).all()
            aliases = (await session.scalars(select(OperatorAlias).order_by(OperatorAlias.operator_id, OperatorAlias.id))).all()
        grouped: dict[int, list[str]] = {}
        for alias in aliases:
            grouped.setdefault(alias.operator_id, []).append(f"{alias.id}:{alias.alias}")
        text = "\n".join(
            f"#{operator.id} | {'فعال' if operator.active else 'غیرفعال'} | {operator.canonical_name}\n  "
            + ", ".join(grouped.get(operator.id, []))
            for operator in rows
        )
        await message.answer(text[:4000] or "اپراتوری وجود ندارد.")

    @router.message(Command("operator_alias_remove"), F.chat.type == "private")
    async def alias_remove(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /operator_alias_remove <alias_id>")
            return
        async with session_factory() as session:
            alias = await session.get(OperatorAlias, int(parts[1]))
            if alias is None:
                await message.answer("Alias یافت نشد.")
                return
            alias_text = alias.alias
            await session.delete(alias)
            await session.commit()
            await audit(session, message.from_user.id, "operator_alias_remove", None, alias_text)
        await message.answer("✅ Alias حذف شد.")

    @router.message(Command("operator_disable"), F.chat.type == "private")
    async def operator_disable(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("استفاده: /operator_disable <operator_id>")
            return
        async with session_factory() as session:
            operator = await session.get(Operator, int(parts[1]))
            if operator is None:
                await message.answer("اپراتور یافت نشد.")
                return
            operator.active = False
            await session.commit()
            await audit(session, message.from_user.id, "operator_disable", None, str(operator.id))
        await message.answer("✅ اپراتور غیرفعال شد؛ سابقه شرکت‌کنندگان حفظ شده است.")

    @router.message(Command("operator_rename"), F.chat.type == "private")
    async def operator_rename(message: Message) -> None:
        if not _is_admin(message, settings):
            return
        parts = _parts(message)
        if len(parts) < 3 or not parts[1].isdigit():
            await message.answer("استفاده: /operator_rename <operator_id> <نام جدید>")
            return
        name = " ".join(parts[2:]).strip()
        async with session_factory() as session:
            operator = await session.get(Operator, int(parts[1]))
            if operator is None:
                await message.answer("اپراتور یافت نشد.")
                return
            if await session.scalar(select(Operator.id).where(Operator.canonical_name == name, Operator.id != operator.id)):
                await message.answer("اپراتوری با این نام وجود دارد.")
                return
            operator.canonical_name = name[:100]
            normalized = normalize_operator_text(name)
            if not await session.scalar(select(OperatorAlias.id).where(OperatorAlias.normalized_alias == normalized)):
                session.add(OperatorAlias(operator_id=operator.id, alias=name[:150], normalized_alias=normalized))
            await session.commit()
            await audit(session, message.from_user.id, "operator_rename", None, f"id={operator.id}; {name}")
        await message.answer("✅ نام استاندارد اپراتور به‌روزرسانی شد.")

    return router
