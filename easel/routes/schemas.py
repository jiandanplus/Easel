"""Request models shared by the core app module and the chat routes.

Pydantic resolves field types when a class is defined, so models referenced in
other models' fields cannot be reached through the late-bound `_core`.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AttachmentRef(BaseModel):
    id: str
    name: str
    path: str


class ChatRequest(BaseModel):
    service_source: str | None = None
    execution_target: str | None = None
    message: str
    persona: str | None = None
    sessionId: str | None = None
    turnId: str | None = None
    attachments: list[AttachmentRef] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=240)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    catalog_revision: str | None = Field(default=None, max_length=160)
