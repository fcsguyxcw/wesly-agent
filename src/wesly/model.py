from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

from wesly.permissions import CommandPurpose


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    tool_name: str
    status: Literal["success", "error"]
    content: str
    error_code: str | None
    target: str
    evidence_paths: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    exit_code: int | None = None
    timed_out: bool | None = None
    output_truncated: bool | None = None
    command_purpose: CommandPurpose | None = None
    change_tracking_complete: bool | None = None


@dataclass(frozen=True, slots=True)
class ModelBudget:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelRequest:
    instructions: tuple[str, ...]
    messages: tuple[Message, ...]
    tools: tuple[Mapping[str, object], ...]
    budget: ModelBudget


@dataclass(frozen=True, slots=True)
class ModelTurn:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    usage: Usage


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelTurn: ...


class ModelProviderError(Exception):
    """A safe, user-facing failure reported by a model adapter."""
