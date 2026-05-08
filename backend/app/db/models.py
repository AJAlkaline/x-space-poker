"""SQLAlchemy ORM models. Mirrors the ERD in docs/data-model.md.

Key design notes:
- All chip amounts are BIGINT (Python int). No floats anywhere near money.
- `accounts.currency_type` is the seam for future crypto/real-money support.
- `ledger_entries` is append-only. `accounts.balance_minor` is a denormalized
  cache of SUM(delta_minor); reconcile nightly.
- Foreign keys do NOT cascade-delete by default — soft delete via status flags.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    x_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    handle: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    accounts: Mapped[list[Account]] = relationship(back_populates="user")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"), index=True)
    currency_type: Mapped[str] = mapped_column(String(16), default="PLAY")
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)

    user: Mapped[User] = relationship(back_populates="accounts")

    __table_args__ = (UniqueConstraint("user_id", "currency_type", name="uq_account_user_currency"),)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("accounts.id"), index=True)
    delta_minor: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(32))   # buy_in | cash_out | pot_win | rake | daily_grant | admin_adjust
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    table_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    hand_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Table(Base):
    __tablename__ = "tables"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    small_blind: Mapped[int] = mapped_column(Integer)
    big_blind: Mapped[int] = mapped_column(Integer)
    min_buyin: Mapped[int] = mapped_column(Integer)
    max_buyin: Mapped[int] = mapped_column(Integer)
    max_seats: Mapped[int] = mapped_column(Integer, default=9)
    rake_bps: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="waiting")  # waiting | active | paused | closed
    host_user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TableSeat(Base):
    __tablename__ = "table_seats"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    table_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("tables.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    seat_number: Mapped[int] = mapped_column(Integer)
    stack: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | sitting_out | disconnected | left

    __table_args__ = (UniqueConstraint("table_id", "seat_number", name="uq_seat_table_seat"),)


class Hand(Base):
    __tablename__ = "hands"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    table_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("tables.id"), index=True)
    hand_number: Mapped[int] = mapped_column(Integer)
    deck_seed_commit: Mapped[str] = mapped_column(String(64))
    deck_seed_reveal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HandAction(Base):
    __tablename__ = "hand_actions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    hand_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("hands.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    action_type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TableEvent(Base):
    __tablename__ = "table_events"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    table_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("tables.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
