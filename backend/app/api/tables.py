"""Tables REST endpoints: create, join, leave, replay.

Two modes:
- `persistence_enabled=False` (default in tests): wallet lives in `auth.py`
  in-memory dict. No DB writes. Existing test infrastructure unchanged.
- `persistence_enabled=True` (production): wallet lives in the `accounts`
  table. Buy-ins write a `LedgerEntry`. Hands persist via the table loop's
  event consumer.

The seat's chip stack is always in-memory (it moves on every action). DB
records the seat at hand boundaries only.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import PlayerId, adjust_balance
from app.core.config import get_settings
from app.db.session import get_session
from app.services import persistence
from app.services.table_manager import get_manager

router = APIRouter()


class CreateTableRequest(BaseModel):
    small_blind: int = Field(default=5, gt=0)
    big_blind: int = Field(default=10, gt=0)
    max_seats: int = Field(default=9, ge=2, le=9)
    narration_enabled: bool = Field(
        default=False,
        description=(
            "If true, the server spawns a TTS narrator that broadcasts "
            "spoken commentary on this table's audio stream. Requires "
            "ELEVENLABS_API_KEY to be set server-side to actually produce "
            "audio; otherwise the narrator runs but emits silent clips."
        ),
    )


class CreateTableResponse(BaseModel):
    table_id: str
    code: str
    narration_enabled: bool = False


@router.post("", response_model=CreateTableResponse)
async def create_table(
    req: CreateTableRequest, player_id: PlayerId,
) -> CreateTableResponse:
    if req.big_blind < req.small_blind * 2:
        raise HTTPException(400, "big_blind must be at least 2x small_blind")
    table_id = uuid.uuid4()
    rt = await get_manager().create_table(
        table_id=str(table_id),
        small_blind=req.small_blind,
        big_blind=req.big_blind,
        max_seats=req.max_seats,
        narration_enabled=req.narration_enabled,
    )
    if get_settings().persistence_enabled:
        async with get_session() as s:
            account = await persistence.ensure_account_for_handle(s, player_id)
            await persistence.persist_table(
                s, table_id=table_id, code=rt.code,
                small_blind=req.small_blind, big_blind=req.big_blind,
                max_seats=req.max_seats, host_user_id=account.user_id,
            )
    return CreateTableResponse(
        table_id=str(table_id), code=rt.code,
        narration_enabled=req.narration_enabled,
    )


class JoinTableRequest(BaseModel):
    code: str
    seat: int = Field(ge=0)
    buy_in: int = Field(gt=0)


class TableInfoResponse(BaseModel):
    table_id: str
    code: str
    small_blind: int
    big_blind: int
    max_seats: int


@router.get("/by-code/{code}", response_model=TableInfoResponse)
async def get_table_by_code(code: str) -> TableInfoResponse:
    rt = get_manager().get_by_code(code)
    if rt is None:
        raise HTTPException(404, "table not found")
    return TableInfoResponse(
        table_id=rt.table_id, code=rt.code,
        small_blind=rt.config.small_blind, big_blind=rt.config.big_blind,
        max_seats=rt.config.max_seats,
    )


@router.post("/join")
async def join_table(req: JoinTableRequest, player_id: PlayerId) -> dict:
    mgr = get_manager()
    rt = mgr.get_by_code(req.code)
    if rt is None:
        raise HTTPException(404, "table not found")
    bb = rt.config.big_blind
    if req.buy_in < 20 * bb or req.buy_in > 200 * bb:
        raise HTTPException(400, f"buy_in must be {20 * bb}-{200 * bb}")

    if get_settings().persistence_enabled:
        # DB-backed flow: debit account, then seat.
        seat_id = uuid.uuid4()
        async with get_session() as s:
            account = await persistence.ensure_account_for_handle(s, player_id)
            try:
                await persistence.buy_in(
                    s, account.id, req.buy_in,
                    seat_id=seat_id, table_id=uuid.UUID(rt.table_id),
                )
            except persistence.InsufficientFundsError as e:
                raise HTTPException(400, str(e)) from e
            try:
                mgr.seat_player(rt.table_id, player_id, req.seat, req.buy_in)
            except ValueError as e:
                # Seat conflict — rollback by NOT committing this transaction.
                # The session's __aexit__ will rollback because we re-raise.
                raise HTTPException(400, str(e)) from e
            await persistence.persist_seat(
                s, seat_id=seat_id, table_id=uuid.UUID(rt.table_id),
                user_id=account.user_id, seat_number=req.seat, stack=req.buy_in,
            )
        return {"table_id": rt.table_id, "seat": req.seat, "stack": req.buy_in}

    # In-memory flow (tests, dev): adjust balance dict directly.
    adjust_balance(player_id, -req.buy_in)
    try:
        mgr.seat_player(rt.table_id, player_id, req.seat, req.buy_in)
    except ValueError as e:
        adjust_balance(player_id, req.buy_in)
        raise HTTPException(400, str(e)) from e
    return {"table_id": rt.table_id, "seat": req.seat, "stack": req.buy_in}


@router.post("/{table_id}/leave")
async def leave_table(table_id: str, player_id: PlayerId) -> dict:
    mgr = get_manager()
    rt = mgr.get(table_id)
    if rt is None:
        raise HTTPException(404, "table not found")
    try:
        stack = mgr.unseat_player(table_id, player_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    if get_settings().persistence_enabled and stack > 0:
        # Cash out via ledger. We need the seat_id but the in-memory unseat
        # has already removed it. For now, key the cashout on table+player
        # (good enough for idempotency at session granularity).
        async with get_session() as s:
            account = await persistence.get_account_for_handle(s, player_id)
            if account is not None:
                # Use a synthetic seat_id derived from player+table for idempotency.
                synthetic = uuid.uuid5(
                    uuid.NAMESPACE_OID,
                    f"cashout:{table_id}:{player_id}",
                )
                await persistence.cash_out(
                    s, account.id, stack,
                    seat_id=synthetic, table_id=uuid.UUID(table_id),
                )
    elif stack > 0:
        adjust_balance(player_id, stack)
    return {"cashed_out": stack}


@router.get("/hands/{hand_id}/replay")
async def get_hand_replay(hand_id: str) -> dict:
    """Return everything needed to replay a completed hand.

    Includes per-action public_state snapshots when start_state was
    captured (i.e. for hands recorded after the start_state column was
    added). Older hands return snapshots=None — the client should fall
    back to a narration-only view.
    """
    if not get_settings().persistence_enabled:
        raise HTTPException(503, "persistence not enabled")
    try:
        hid = uuid.UUID(hand_id)
    except ValueError as e:
        raise HTTPException(400, "invalid hand_id") from e
    async with get_session() as s:
        replay = await persistence.get_hand_for_replay(s, hid)
        if replay is None:
            raise HTTPException(404, "hand not found or not yet complete")

    # Reconstruct snapshots outside the DB session (engine work, no I/O).
    from app.services.replay import reconstruct_snapshots
    replay["snapshots"] = reconstruct_snapshots(replay)
    return replay


@router.get("/{code}/hands")
async def list_table_hands(code: str, limit: int = 20) -> dict:
    """List recently completed hands at a table, newest first.

    Used by the lobby's "recent hands" section. Each entry has the hand_id
    and minimal metadata — clients can fetch the full replay separately.
    """
    if not get_settings().persistence_enabled:
        raise HTTPException(503, "persistence not enabled")
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")
    async with get_session() as s:
        # Resolve table by code first.
        from sqlalchemy import select

        from app.db.models import Hand as HandModel
        from app.db.models import Table as TableModel
        result = await s.execute(
            select(TableModel).where(TableModel.code == code),
        )
        table_row = result.scalar_one_or_none()
        if table_row is None:
            raise HTTPException(404, "table not found")

        # Most recent completed hands (deck_seed_reveal IS NOT NULL).
        result = await s.execute(
            select(HandModel)
            .where(HandModel.table_id == table_row.id)
            .where(HandModel.deck_seed_reveal.isnot(None))
            .order_by(HandModel.started_at.desc())
            .limit(limit),
        )
        hands = result.scalars().all()
        return {
            "code": code,
            "hands": [
                {
                    "hand_id": str(h.id),
                    "hand_number": h.hand_number,
                    "started_at": h.started_at.isoformat() if h.started_at else None,
                }
                for h in hands
            ],
        }
