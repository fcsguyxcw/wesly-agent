from collections.abc import Sequence
from pathlib import Path

from wesly.agent import Agent
from wesly.context import ReadOnlyContextBuilder
from wesly.events import (
    ModelCompleted,
    ModelStarted,
    RunCompleted,
    RunFailed,
    ToolCompleted,
    ToolStarted,
)
from wesly.model import ModelRequest, ModelTurn, ToolCall, Usage
from wesly.tools import ToolRegistry


class RecordingModelClient:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        return next(self._turns)


def test_agent_lists_a_real_workspace_and_returns_the_result_to_model(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("Wesly", encoding="utf-8")
    client = RecordingModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="list_workspace",
                        arguments_json='{"path":"."}',
                    ),
                ),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=10, output_tokens=4),
            ),
            ModelTurn(
                content="项目包含 src 目录和 README.md。",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=18, output_tokens=7),
            ),
        ]
    )
    agent = Agent(
        client,
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
    )

    events = list(agent.run("这个项目有什么？"))

    assert events == [
        ModelStarted(turn=1),
        ModelCompleted(
            turn=1,
            finish_reason="tool_calls",
            usage=Usage(input_tokens=10, output_tokens=4),
        ),
        ToolStarted(call_id="call-1", tool_name="list_workspace", target="."),
        ToolCompleted(
            call_id="call-1",
            tool_name="list_workspace",
            status="success",
            target=".",
            error_code=None,
        ),
        ModelStarted(turn=2),
        ModelCompleted(
            turn=2,
            finish_reason="stop",
            usage=Usage(input_tokens=18, output_tokens=7),
        ),
        RunCompleted(
            answer="项目包含 src 目录和 README.md。",
            model_turns=2,
            tool_calls=1,
            usage=Usage(input_tokens=28, output_tokens=11),
        ),
    ]
    second_request = client.requests[1]
    assert second_request.messages[-2].tool_calls == (
        ToolCall(
            id="call-1",
            name="list_workspace",
            arguments_json='{"path":"."}',
        ),
    )
    assert second_request.messages[-1].role == "tool"
    assert second_request.messages[-1].tool_call_id == "call-1"
    assert '"README.md"' in (second_request.messages[-1].content or "")
    assert '"src"' in (second_request.messages[-1].content or "")


def test_agent_returns_validation_failures_to_the_model(tmp_path: Path) -> None:
    client = RecordingModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(
                    ToolCall("bad-json", "list_workspace", "{"),
                    ToolCall("unknown", "unknown_tool", "{}"),
                    ToolCall("escape", "list_workspace", '{"path":".."}'),
                ),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=6, output_tokens=3),
            ),
            ModelTurn(
                content="这些请求不安全，已停止。",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=13, output_tokens=5),
            ),
        ]
    )
    agent = Agent(
        client,
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
    )

    events = list(agent.run("检查外部目录"))

    completed = [event for event in events if isinstance(event, ToolCompleted)]
    assert [event.status for event in completed] == ["error", "error", "error"]
    assert [event.error_code for event in completed] == [
        "invalid_json",
        "unknown_tool",
        "permission_denied",
    ]
    assert isinstance(events[-1], RunCompleted)
    assert events[-1].tool_calls == 3


def test_agent_enforces_the_tool_call_limit(tmp_path: Path) -> None:
    client = RecordingModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(
                    ToolCall("call-1", "list_workspace", "{}"),
                    ToolCall("call-2", "list_workspace", "{}"),
                ),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=3, output_tokens=2),
            )
        ]
    )
    agent = Agent(
        client,
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
        max_tool_calls=1,
    )

    events = list(agent.run("列目录"))

    assert events[-1] == RunFailed(
        stop_reason="tool_limit",
        message="已达到工具调用上限",
        model_turns=1,
        tool_calls=1,
    )


def test_agent_enforces_the_model_turn_limit(tmp_path: Path) -> None:
    client = RecordingModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(ToolCall("call-1", "list_workspace", "{}"),),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=3, output_tokens=2),
            )
        ]
    )
    agent = Agent(
        client,
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
        max_model_turns=1,
    )

    events = list(agent.run("列目录"))

    assert events[-1] == RunFailed(
        stop_reason="turn_limit",
        message="已达到模型轮次上限",
        model_turns=1,
        tool_calls=1,
    )
