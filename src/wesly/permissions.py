from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


ApprovalDecision = Literal["allow_once", "deny"]
ApprovalDecisionReason = Literal[
    "user",
    "timeout",
    "interrupted",
    "approval_error",
]
FileEffectKind = Literal["write_text", "write_binary", "delete", "move"]
Sensitivity = Literal[
    "normal",
    "sensitive",
    "workspace_external",
    "sensitive_workspace_external",
]


@dataclass(frozen=True, slots=True)
class NormalizedFileEffect:
    kind: FileEffectKind
    requested_path: str
    target: Path
    requested_destination: str | None
    destination: Path | None
    content: bytes | None
    effect: str
    sensitivity: Sensitivity
    previous_sha256: str | None


@dataclass(frozen=True, slots=True)
class PreparedOperation:
    call_id: str
    arguments_json: str
    fingerprint: str
    operation: str
    parameters: str
    resolved_targets: tuple[str, ...]
    reason: str
    impact_scope: str
    workspace: str
    sensitivity: Sensitivity
    effects: tuple[NormalizedFileEffect, ...]


class ApprovalProvider(Protocol):
    def decide(self, operation: PreparedOperation) -> ApprovalDecision: ...


class ApprovalTimedOutError(Exception):
    """The approval provider did not receive a decision before its deadline."""
