"""Initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("x_user_id", sa.String(64), unique=True, nullable=False),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_x_user_id", "users", ["x_user_id"])

    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("currency_type", sa.String(16), nullable=False, server_default="PLAY"),
        sa.Column("balance_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "currency_type", name="uq_account_user_currency"),
    )
    op.create_index("ix_accounts_user_id", "accounts", ["user_id"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("delta_minor", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), unique=True, nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("hand_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ledger_account_id", "ledger_entries", ["account_id"])
    op.create_index("ix_ledger_idempotency_key", "ledger_entries", ["idempotency_key"])

    op.create_table(
        "tables",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(8), unique=True, nullable=False),
        sa.Column("small_blind", sa.Integer, nullable=False),
        sa.Column("big_blind", sa.Integer, nullable=False),
        sa.Column("min_buyin", sa.Integer, nullable=False),
        sa.Column("max_buyin", sa.Integer, nullable=False),
        sa.Column("max_seats", sa.Integer, nullable=False, server_default="9"),
        sa.Column("rake_bps", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="waiting"),
        sa.Column("host_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tables_code", "tables", ["code"])

    op.create_table(
        "table_seats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tables.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("seat_number", sa.Integer, nullable=False),
        sa.Column("stack", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.UniqueConstraint("table_id", "seat_number", name="uq_seat_table_seat"),
    )
    op.create_index("ix_table_seats_table_id", "table_seats", ["table_id"])

    op.create_table(
        "hands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tables.id"), nullable=False),
        sa.Column("hand_number", sa.Integer, nullable=False),
        sa.Column("deck_seed_commit", sa.String(64), nullable=False),
        sa.Column("deck_seed_reveal", sa.String(64), nullable=True),
        sa.Column("final_state", postgresql.JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hands_table_id", "hands", ["table_id"])

    op.create_table(
        "hand_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hands.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action_type", sa.String(16), nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hand_actions_hand_id", "hand_actions", ["hand_id"])

    op.create_table(
        "table_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tables.id"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_table_events_table_id", "table_events", ["table_id"])


def downgrade() -> None:
    for tbl in ["table_events", "hand_actions", "hands", "table_seats", "tables", "ledger_entries", "accounts", "users"]:
        op.drop_table(tbl)
