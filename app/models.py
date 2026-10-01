import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GiveawayStatus(str, enum.Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class AttemptResult(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    INVALID_OPERATOR = "INVALID_OPERATOR"
    NOT_MEMBER = "NOT_MEMBER"
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
    WRONG_THREAD = "WRONG_THREAD"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class Giveaway(Base):
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="AsanPanel Giveaway")
    status: Mapped[GiveawayStatus] = mapped_column(Enum(GiveawayStatus), default=GiveawayStatus.IDLE)
    channel_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OperatorAlias(Base):
    __tablename__ = "operator_aliases"
    __table_args__ = (UniqueConstraint("normalized_alias", name="uq_operator_alias_normalized"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(150), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(150), nullable=False)


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("giveaway_id", "telegram_user_id", name="uq_participant_per_giveaway"),
        Index("ix_participant_giveaway_eligible", "giveaway_id", "eligible"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(ForeignKey("giveaways.id", ondelete="RESTRICT"), nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str] = mapped_column(String(256), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id", ondelete="RESTRICT"), nullable=False)
    operator_raw: Mapped[str] = mapped_column(String(500), nullable=False)
    membership_at_entry: Mapped[bool] = mapped_column(Boolean, nullable=False)
    membership_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    membership_last_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    ineligibility_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class MessageAttempt(Base):
    __tablename__ = "message_attempts"
    __table_args__ = (Index("ix_attempt_giveaway_result", "giveaway_id", "result"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    giveaway_id: Mapped[int | None] = mapped_column(ForeignKey("giveaways.id", ondelete="SET NULL"), nullable=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[AttemptResult] = mapped_column(Enum(AttemptResult), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Winner(Base):
    __tablename__ = "winners"
    __table_args__ = (UniqueConstraint("giveaway_id", "rank", name="uq_winner_rank"), UniqueConstraint("giveaway_id", "participant_id", name="uq_winner_participant"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(ForeignKey("giveaways.id", ondelete="RESTRICT"), nullable=False)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="RESTRICT"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    prize: Mapped[str] = mapped_column(String(50), nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    giveaway_id: Mapped[int | None] = mapped_column(ForeignKey("giveaways.id", ondelete="SET NULL"), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
