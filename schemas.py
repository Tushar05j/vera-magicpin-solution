from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Scope = Literal["category", "merchant", "customer", "trigger"]


class ContextPush(BaseModel):
    scope: Scope
    context_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    delivered_at: str
    payload: dict[str, Any]


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str = Field(min_length=1)
    message: str = Field(min_length=1)
    received_at: str
    turn_number: int = Field(ge=1)