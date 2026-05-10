"""Add start_state to hands for replay viewer

Revision ID: 0002_hand_start_state
Revises: 0001_initial
Create Date: 2026-01-20 00:00:00.000000

The replay viewer needs a known initial hand state to drive the engine
forward from. Rather than reconstructing it from seats and other
table-level data (which can drift between hand starts), we capture the
public_view at hand-start time and store it here.

Old rows have NULL — the API can detect this and fall back to a
narration-only replay view on the client.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_hand_start_state"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hands",
        sa.Column("start_state", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hands", "start_state")
