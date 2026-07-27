from collections.abc import Sequence
from pathlib import Path

from wesly.agent import Agent
from wesly.context import ReadOnlyContextBuilder
from wesly.events import RunCompleted, RunFailed
from wesly.model import ModelRequest, ModelTurn, ToolCall, Usage
from wesly.tools import ToolRegistry


class ScriptedModelClient:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)

    def complete(self, request: ModelRequest) -> ModelTurn:
        return next(self._turns)


def turn(*calls: ToolCall, content: str | None = None) -> ModelTurn:
    return ModelTurn(
        content=content,
        tool_calls=calls,
        finish_reason="tool_calls" if calls else "stop",
        usage=Usage(input_tokens=3, output_tokens=2),
    )


def make_agent(tmp_path: Path, turns: Sequence[ModelTurn]) -> Agent:
    return Agent(
        ScriptedModelClient(turns),
        context_builder=ReadOnlyContextBuilder(tmp_path),
        tool_registry=ToolRegistry(tmp_path),
    )


def test_agent_combines_list_search_and_read_before_citing_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    agent = make_agent(
        tmp_path,
        [
            turn(ToolCall("list", "list_workspace", "{}")),
            turn(ToolCall("search", "search_text", '{"query":"main","path":"."}')),
            turn(ToolCall("read", "read_file", '{"path":"app.py"}')),
            turn(content="入口定义在 [[app.py]]。"),
        ],
    )

    events = list(agent.run("入口在哪里？"))

    assert events[-1] == RunCompleted(
        answer="入口定义在 [[app.py]]。",
        model_turns=4,
        tool_calls=3,
        usage=Usage(input_tokens=12, output_tokens=8),
        evidence_paths=("app.py",),
    )


def test_agent_supports_multiple_tools_in_one_model_turn(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    agent = make_agent(
        tmp_path,
        [
            turn(
                ToolCall("search", "search_text", '{"query":"needle"}'),
                ToolCall("read", "read_file", '{"path":"a.py"}'),
            ),
            turn(content="结果见 [[a.py]]。"),
        ],
    )

    events = list(agent.run("查找 needle"))

    assert isinstance(events[-1], RunCompleted)
    assert events[-1].tool_calls == 2
    assert events[-1].evidence_paths == ("a.py",)


def test_agent_fails_when_answer_cites_unobserved_file(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, [turn(content="答案见 [[missing.py]]。")])

    events = list(agent.run("入口在哪里？"))

    assert events[-1] == RunFailed(
        stop_reason="evidence_error",
        message="模型引用了本次运行未观察的文件: missing.py",
        model_turns=1,
        tool_calls=0,
    )


def test_agent_ignores_citation_syntax_examples_inside_code(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    agent = make_agent(
        tmp_path,
        [
            turn(ToolCall("read", "read_file", '{"path":"app.py"}')),
            turn(
                content=(
                    "引用语法示例是 `[[path/to/file]]`。\n"
                    "```text\n[[...]]\n```\n"
                    "实际依据见 [[app.py]]。"
                )
            ),
        ],
    )

    events = list(agent.run("读取 app.py"))

    assert isinstance(events[-1], RunCompleted)
    assert events[-1].evidence_paths == ("app.py",)
