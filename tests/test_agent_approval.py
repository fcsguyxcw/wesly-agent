import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from wesly.agent import Agent
from wesly.context import ReadOnlyContextBuilder
from wesly.events import (
    ApprovalDecided,
    ApprovalRequested,
    RunCompleted,
    RunFailed,
    ToolCompleted,
)
from wesly.model import ModelRequest, ModelTurn, ToolCall, Usage
from wesly.permissions import ApprovalDecision, ApprovalTimedOutError, PreparedOperation
from wesly.tools import ToolRegistry


class RecordingModelClient:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        return next(self._turns)


class FixedApprovalProvider:
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests: list[PreparedOperation] = []

    def decide(self, operation: PreparedOperation) -> ApprovalDecision:
        self.requests.append(operation)
        return self.decision


def high_risk_call() -> ToolCall:
    return ToolCall(
        "effects",
        "apply_file_operations",
        json.dumps(
            {
                "reason": "创建结果文件",
                "operations": [
                    {"kind": "write_text", "path": "result.txt", "content": "done"}
                ],
            },
            ensure_ascii=False,
        ),
    )


def scripted_client() -> RecordingModelClient:
    return RecordingModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(high_risk_call(),),
                finish_reason="tool_calls",
                usage=Usage(5, 2),
            ),
            ModelTurn(
                content="已根据审批结果继续。",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(7, 3),
            ),
        ]
    )


def test_agent_denial_returns_structured_tool_result_and_continues(
    tmp_path: Path,
) -> None:
    approval = FixedApprovalProvider("deny")
    client = scripted_client()
    agent = Agent(
        client,
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
        approval_provider=approval,
    )

    events = list(agent.run("创建结果文件"))

    assert len(approval.requests) == 1
    assert not (tmp_path / "result.txt").exists()
    assert [type(event) for event in events if isinstance(event, (ApprovalRequested, ApprovalDecided))] == [
        ApprovalRequested,
        ApprovalDecided,
    ]
    decision = next(event for event in events if isinstance(event, ApprovalDecided))
    assert decision.decision == "deny"
    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert completed.error_code == "permission_denied"
    tool_payload = json.loads(client.requests[1].messages[-1].content or "{}")
    assert tool_payload["error_code"] == "permission_denied"
    assert isinstance(events[-1], RunCompleted)


def test_agent_allow_once_executes_exact_operation(tmp_path: Path) -> None:
    approval = FixedApprovalProvider("allow_once")
    agent = Agent(
        scripted_client(),
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
        approval_provider=approval,
    )

    events = list(agent.run("创建结果文件"))

    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "done"
    decision = next(event for event in events if isinstance(event, ApprovalDecided))
    assert decision.decision == "allow_once"
    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert completed.status == "success"


def test_concurrent_drift_after_approval_is_auditable_and_has_no_effect(
    tmp_path: Path,
) -> None:
    approval = FixedApprovalProvider("allow_once")

    def drift_after_decision(event: object) -> None:
        if isinstance(event, ApprovalDecided):
            (tmp_path / "result.txt").write_text("user content", encoding="utf-8")

    agent = Agent(
        scripted_client(),
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
        approval_provider=approval,
        approval_audit=drift_after_decision,
    )

    events = list(agent.run("创建结果文件"))

    approval_requested = next(
        index for index, event in enumerate(events) if isinstance(event, ApprovalRequested)
    )
    approval_decided = next(
        index for index, event in enumerate(events) if isinstance(event, ApprovalDecided)
    )
    tool_completed = next(
        index for index, event in enumerate(events) if isinstance(event, ToolCompleted)
    )
    assert approval_requested < approval_decided < tool_completed
    completed = events[tool_completed]
    assert isinstance(completed, ToolCompleted)
    assert completed.error_code == "operation_drift"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "user content"


def test_approval_timeout_denies_and_returns_control_to_model(tmp_path: Path) -> None:
    class TimingOutApprovalProvider:
        def decide(self, operation: PreparedOperation) -> ApprovalDecision:
            raise ApprovalTimedOutError

    client = scripted_client()
    agent = Agent(
        client,
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
        approval_provider=TimingOutApprovalProvider(),
    )

    events = list(agent.run("创建结果文件"))

    decision = next(event for event in events if isinstance(event, ApprovalDecided))
    assert decision.decision == "deny"
    assert decision.reason == "timeout"
    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert completed.error_code == "permission_denied"
    assert not (tmp_path / "result.txt").exists()
    assert isinstance(events[-1], RunCompleted)


def test_approval_provider_error_fails_closed(tmp_path: Path) -> None:
    class BrokenApprovalProvider:
        def decide(self, operation: PreparedOperation) -> ApprovalDecision:
            raise RuntimeError("broken provider")

    agent = Agent(
        scripted_client(),
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
        approval_provider=BrokenApprovalProvider(),
    )

    events = list(agent.run("创建结果文件"))

    decision = next(event for event in events if isinstance(event, ApprovalDecided))
    assert decision.decision == "deny"
    assert decision.reason == "approval_error"
    assert not (tmp_path / "result.txt").exists()


def test_approval_audit_failure_stops_before_side_effect(tmp_path: Path) -> None:
    def broken_audit(event: object) -> None:
        if isinstance(event, ApprovalDecided):
            raise OSError("audit unavailable")

    registry = ToolRegistry(tmp_path)
    approval = FixedApprovalProvider("allow_once")
    agent = Agent(
        scripted_client(),
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=registry,
        approval_provider=approval,
        approval_audit=broken_audit,
    )

    events = list(agent.run("创建结果文件"))

    assert not (tmp_path / "result.txt").exists()
    assert isinstance(events[-1], RunFailed)
    assert events[-1].stop_reason == "permission_error"
    assert not any(isinstance(event, ToolCompleted) for event in events)
    replay = registry.execute(
        high_risk_call(),
        approved_operation=approval.requests[0],
    )
    assert replay.error_code == "permission_denied"


def test_approval_interrupt_revokes_pending_operation(tmp_path: Path) -> None:
    class InterruptingApprovalProvider:
        def __init__(self) -> None:
            self.operation: PreparedOperation | None = None

        def decide(self, operation: PreparedOperation) -> ApprovalDecision:
            self.operation = operation
            raise KeyboardInterrupt

    registry = ToolRegistry(tmp_path)
    approval = InterruptingApprovalProvider()
    agent = Agent(
        scripted_client(),
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=registry,
        approval_provider=approval,
    )

    events = list(agent.run("创建结果文件"))

    assert isinstance(events[-1], RunFailed)
    assert events[-1].stop_reason == "interrupted"
    assert approval.operation is not None
    replay = registry.execute(
        high_risk_call(),
        approved_operation=approval.operation,
    )
    assert replay.error_code == "permission_denied"
    assert not (tmp_path / "result.txt").exists()


def test_permission_policy_error_stops_before_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ToolRegistry(tmp_path)

    def broken_policy(call: ToolCall) -> PreparedOperation | None:
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(registry, "prepare_operation", broken_policy)
    agent = Agent(
        scripted_client(),
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=registry,
        approval_provider=FixedApprovalProvider("allow_once"),
    )

    events = list(agent.run("创建结果文件"))

    assert not (tmp_path / "result.txt").exists()
    assert isinstance(events[-1], RunFailed)
    assert events[-1].stop_reason == "permission_error"
    assert not any(isinstance(event, ApprovalRequested) for event in events)
