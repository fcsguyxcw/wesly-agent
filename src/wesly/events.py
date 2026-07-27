from dataclasses import dataclass
from typing import Literal

from wesly.model import Usage
from wesly.permissions import (
    ApprovalDecision,
    ApprovalDecisionReason,
    CommandPurpose,
    Sensitivity,
)


@dataclass(frozen=True, slots=True)
class ModelStarted:
    turn: int


@dataclass(frozen=True, slots=True)
class ModelCompleted:
    turn: int
    finish_reason: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class ToolStarted:
    call_id: str
    tool_name: str
    target: str


@dataclass(frozen=True, slots=True)
class FileDiffProposed:
    call_id: str
    path: str
    diff: str


@dataclass(frozen=True, slots=True)
class ApprovalRequested:
    call_id: str
    fingerprint: str
    operation: str
    parameters: str
    resolved_targets: tuple[str, ...]
    reason: str
    impact_scope: str
    workspace: str
    sensitivity: Sensitivity


@dataclass(frozen=True, slots=True)
class ApprovalDecided:
    call_id: str
    fingerprint: str
    decision: ApprovalDecision
    reason: ApprovalDecisionReason


@dataclass(frozen=True, slots=True)
class ToolCompleted:
    call_id: str
    tool_name: str
    status: Literal["success", "error"]
    target: str
    error_code: str | None
    changed_paths: tuple[str, ...] = ()
    exit_code: int | None = None
    timed_out: bool | None = None
    output_truncated: bool | None = None
    command_purpose: CommandPurpose | None = None
    change_tracking_complete: bool | None = None


@dataclass(frozen=True, slots=True)
class RunCompleted:
    answer: str
    model_turns: int
    tool_calls: int
    usage: Usage
    evidence_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunFailed:
    stop_reason: str
    message: str
    model_turns: int
    tool_calls: int


AgentEvent = (
    ModelStarted
    | ModelCompleted
    | ToolStarted
    | FileDiffProposed
    | ApprovalRequested
    | ApprovalDecided
    | ToolCompleted
    | RunCompleted
    | RunFailed
)
