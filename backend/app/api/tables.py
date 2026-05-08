"""Tables REST endpoints: create a table, join by code, leave a seat."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


class CreateTableRequest(BaseModel):
    small_blind: int = Field(gt=0)
    big_blind: int = Field(gt=0)
    max_seats: int = Field(default=9, ge=2, le=9)


class CreateTableResponse(BaseModel):
    table_id: str
    code: str


@router.post("", response_model=CreateTableResponse)
async def create_table(req: CreateTableRequest):
    if req.big_blind < req.small_blind * 2:
        raise HTTPException(400, "big_blind must be at least 2x small_blind")
    # TODO: auth, persist Table row, register with TableManager
    raise HTTPException(501, "not implemented")


class JoinTableRequest(BaseModel):
    code: str
    seat: int = Field(ge=0)
    buy_in: int = Field(gt=0)


@router.post("/join")
async def join_table(req: JoinTableRequest):
    # TODO: auth, validate seat available, debit account, seat the player
    raise HTTPException(501, "not implemented")


@router.post("/{table_id}/leave")
async def leave_table(table_id: str):
    # TODO: auth, cash out remaining stack to account, remove from TableManager
    raise HTTPException(501, "not implemented")
