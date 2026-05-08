"""Tables REST endpoints: create a table, join by code, leave a seat."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import PlayerId, adjust_balance
from app.services.table_manager import get_manager

router = APIRouter()


class CreateTableRequest(BaseModel):
    small_blind: int = Field(default=5, gt=0)
    big_blind: int = Field(default=10, gt=0)
    max_seats: int = Field(default=9, ge=2, le=9)


class CreateTableResponse(BaseModel):
    table_id: str
    code: str


@router.post("", response_model=CreateTableResponse)
async def create_table(req: CreateTableRequest, _: PlayerId) -> CreateTableResponse:
    if req.big_blind < req.small_blind * 2:
        raise HTTPException(400, "big_blind must be at least 2x small_blind")
    table_id = str(uuid.uuid4())
    rt = await get_manager().create_table(
        table_id=table_id,
        small_blind=req.small_blind,
        big_blind=req.big_blind,
        max_seats=req.max_seats,
    )
    return CreateTableResponse(table_id=table_id, code=rt.code)


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
        table_id=rt.table_id,
        code=rt.code,
        small_blind=rt.config.small_blind,
        big_blind=rt.config.big_blind,
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
    # Debit wallet first; if seating fails, refund.
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
    if stack > 0:
        adjust_balance(player_id, stack)
    return {"cashed_out": stack}
