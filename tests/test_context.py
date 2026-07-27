from collections.abc import Mapping
from pathlib import Path
from typing import cast

from wesly.context import DirectAnswerContextBuilder, ReadOnlyContextBuilder
from wesly.model import Message, ModelBudget, ModelRequest


def test_direct_answer_builder_uses_the_stable_model_request() -> None:
    builder = DirectAnswerContextBuilder()

    request = builder.build("这个项目是什么？")

    assert request == ModelRequest(
        instructions=(
            "You are Wesly, a local personal coding agent. "
            "Answer in the user's language.",
        ),
        messages=(Message(role="user", content="这个项目是什么？"),),
        tools=(),
        budget=ModelBudget(),
    )


def test_read_only_context_exposes_the_three_tools_and_citation_contract(
    tmp_path: Path,
) -> None:
    request = ReadOnlyContextBuilder(tmp_path).build("检查入口")

    function_definitions = [
        cast(Mapping[str, object], tool["function"]) for tool in request.tools
    ]
    tool_names = [definition["name"] for definition in function_definitions]
    assert tool_names == ["list_workspace", "search_text", "read_file"]
    assert "[[workspace/relative/path]]" in request.instructions[0]
