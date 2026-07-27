from collections.abc import Sequence
from pathlib import Path

from wesly.agent import Agent
from wesly.context import INPUT_TOKEN_BUDGET, ReadOnlyContextBuilder
from wesly.events import ModelCompleted, ModelStarted, RunCompleted, RunFailed
from wesly.model import ModelProviderError, ModelRequest, ModelTurn, Usage


class ScriptedModelClient:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)

    def complete(self, request: ModelRequest) -> ModelTurn:
        return next(self._turns)


def test_agent_returns_a_direct_model_answer() -> None:
    client = ScriptedModelClient(
        [
            ModelTurn(
                content="这是一个 Python 项目。",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=12, output_tokens=8),
            )
        ]
    )

    events = list(Agent(client).run("这个项目是什么？"))

    assert events == [
        ModelStarted(turn=1),
        ModelCompleted(
            turn=1,
            finish_reason="stop",
            usage=Usage(input_tokens=12, output_tokens=8),
        ),
        RunCompleted(
            answer="这是一个 Python 项目。",
            model_turns=1,
            tool_calls=0,
            usage=Usage(input_tokens=12, output_tokens=8),
        ),
    ]


class FailingModelClient:
    def complete(self, request: ModelRequest) -> ModelTurn:
        raise ModelProviderError("模型服务暂时不可用")


def test_agent_reports_a_provider_failure() -> None:
    events = list(Agent(FailingModelClient()).run("检查这个项目"))

    assert events == [
        ModelStarted(turn=1),
        RunFailed(
            stop_reason="provider_error",
            message="模型服务暂时不可用",
            model_turns=1,
            tool_calls=0,
        ),
    ]


def test_agent_rejects_an_empty_direct_answer() -> None:
    client = ScriptedModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=7, output_tokens=0),
            )
        ]
    )

    events = list(Agent(client).run("检查项目"))

    assert events == [
        ModelStarted(turn=1),
        ModelCompleted(
            turn=1,
            finish_reason="stop",
            usage=Usage(input_tokens=7, output_tokens=0),
        ),
        RunFailed(
            stop_reason="provider_error",
            message="模型响应不包含答案",
            model_turns=1,
            tool_calls=0,
        ),
    ]


class MustNotBeCalledModelClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelTurn:
        self.calls += 1
        raise AssertionError("model must not be called after context limit")


def test_agent_stops_before_model_call_when_context_exceeds_budget(
    tmp_path: Path,
) -> None:
    client = MustNotBeCalledModelClient()
    agent = Agent(
        client,
        context_builder=ReadOnlyContextBuilder(
            tmp_path,
            global_instructions_path=tmp_path / "missing-global.md",
        ),
    )

    events = list(agent.run("x" * (INPUT_TOKEN_BUDGET * 3 + 1)))

    assert client.calls == 0
    assert len(events) == 1
    assert isinstance(events[0], RunFailed)
    assert events[0].stop_reason == "context_limit"
    assert events[0].model_turns == 0
    assert "estimated_input_tokens=" in events[0].message


class InterruptingModelClient:
    def complete(self, request: ModelRequest) -> ModelTurn:
        raise KeyboardInterrupt


def test_agent_maps_keyboard_interrupt_to_an_explicit_stop() -> None:
    events = list(Agent(InterruptingModelClient()).run("检查项目"))

    assert events == [
        ModelStarted(turn=1),
        RunFailed(
            stop_reason="interrupted",
            message="任务已由用户中断",
            model_turns=0,
            tool_calls=0,
        ),
    ]
