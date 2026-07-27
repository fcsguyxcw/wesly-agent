import json
from collections.abc import Sequence
from pathlib import Path

from wesly.agent import Agent
from wesly.context import ReadOnlyContextBuilder
from wesly.events import ApprovalDecided, ApprovalRequested, RunCompleted, ToolCompleted
from wesly.model import ModelRequest, ModelTurn, ToolCall, Usage
from wesly.permissions import ApprovalDecision, PreparedOperation
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
