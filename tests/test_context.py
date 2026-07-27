from collections.abc import Mapping
import hashlib
from pathlib import Path
from typing import cast

import pytest

from wesly.context import (
    CONTEXT_POLICY_VERSION,
    ContextLimitError,
    DirectAnswerContextBuilder,
    INPUT_TOKEN_BUDGET,
    InstructionLoadError,
    OUTPUT_TOKEN_BUDGET,
    ReadOnlyContextBuilder,
)
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
    assert all("next_cursor" in str(definition["description"]) for definition in function_definitions)
    assert "actual observed workspace-relative path" in request.instructions[0]
    assert "[[" not in request.instructions[0]
    assert "continue with its next_cursor" in request.instructions[0]
    assert CONTEXT_POLICY_VERSION in request.instructions[1]
    assert request.budget == ModelBudget(
        input_tokens=INPUT_TOKEN_BUDGET,
        output_tokens=OUTPUT_TOKEN_BUDGET,
    )


def test_chronological_context_preserves_current_task_then_history(
    tmp_path: Path,
) -> None:
    history = (
        Message(role="assistant", content=None),
        Message(role="tool", content='{"page":1}', tool_call_id="call-1"),
        Message(role="assistant", content="继续调查"),
    )

    request = ReadOnlyContextBuilder(
        tmp_path,
        global_instructions_path=tmp_path / "missing-global.md",
    ).build("当前任务", history)

    assert request.messages == (Message(role="user", content="当前任务"), *history)


def test_context_limit_reports_budget_composition(tmp_path: Path) -> None:
    builder = ReadOnlyContextBuilder(
        tmp_path,
        global_instructions_path=tmp_path / "missing-global.md",
    )

    with pytest.raises(ContextLimitError) as raised:
        builder.build("x" * (INPUT_TOKEN_BUDGET * 3 + 1))

    assert raised.value.estimated_input_tokens > INPUT_TOKEN_BUDGET
    assert raised.value.input_token_budget == INPUT_TOKEN_BUDGET
    assert raised.value.largest_component == "messages"
    assert "instructions=" in str(raised.value)
    assert "messages=" in str(raised.value)
    assert "tools=" in str(raised.value)


def test_read_only_context_loads_scoped_instructions_with_metadata_and_precedence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "src" / "feature"
    nested.mkdir(parents=True)
    global_file = tmp_path / "global" / "WESLY.md"
    global_file.parent.mkdir()
    global_file.write_text("global rule", encoding="utf-8")
    (workspace / "WESLY.md").write_text("root rule", encoding="utf-8")
    (workspace / "src" / "WESLY.md").write_text("ancestor rule", encoding="utf-8")
    (nested / "WESLY.md").write_text("nearest rule", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("must not load", encoding="utf-8")

    request = ReadOnlyContextBuilder(
        workspace,
        global_instructions_path=global_file,
    ).build("follow the user")

    scoped = request.instructions[2:]
    assert ["nearest rule" in block for block in scoped] == [True, False, False, False]
    assert ["ancestor rule" in block for block in scoped] == [False, True, False, False]
    assert ["root rule" in block for block in scoped] == [False, False, True, False]
    assert ["global rule" in block for block in scoped] == [False, False, False, True]
    assert all(
        '"source":' in block and '"scope":' in block and '"sha256":' in block
        for block in scoped
    )
    assert hashlib.sha256(b"nearest rule").hexdigest() in scoped[0]
    assert "must not load" not in "\n".join(request.instructions)
    assert "current user request" in request.instructions[0]


def test_instruction_snapshot_is_pinned_when_disk_changes(tmp_path: Path) -> None:
    instructions = tmp_path / "WESLY.md"
    instructions.write_text("old rule", encoding="utf-8")
    builder = ReadOnlyContextBuilder(
        tmp_path,
        global_instructions_path=tmp_path / "missing-global.md",
    )

    instructions.write_text("new rule", encoding="utf-8")

    assert "old rule" in "\n".join(builder.build("first").instructions)
    assert "old rule" in "\n".join(builder.build("second").instructions)
    assert "new rule" in "\n".join(
        ReadOnlyContextBuilder(
            tmp_path,
            global_instructions_path=tmp_path / "missing-global.md",
        ).build("new session").instructions
    )


def test_instruction_scan_skips_conventional_directories(tmp_path: Path) -> None:
    ignored = tmp_path / "node_modules" / "package"
    ignored.mkdir(parents=True)
    (ignored / "WESLY.md").write_text("ignored rule", encoding="utf-8")

    request = ReadOnlyContextBuilder(
        tmp_path,
        global_instructions_path=tmp_path / "missing-global.md",
    ).build("inspect")

    assert "ignored rule" not in "\n".join(request.instructions)


def test_instruction_scan_rejects_invalid_utf8_and_external_symlink(
    tmp_path: Path,
) -> None:
    invalid_workspace = tmp_path / "invalid"
    invalid_workspace.mkdir()
    (invalid_workspace / "WESLY.md").write_bytes(b"\xff")
    with pytest.raises(InstructionLoadError, match="UTF-8"):
        ReadOnlyContextBuilder(
            invalid_workspace,
            global_instructions_path=tmp_path / "missing-global.md",
        )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside rule", encoding="utf-8")
    link = workspace / "WESLY.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("host does not permit file symlinks")
    with pytest.raises(InstructionLoadError, match="工作区外"):
        ReadOnlyContextBuilder(
            workspace,
            global_instructions_path=tmp_path / "missing-global.md",
        )


def test_instruction_limit_fails_atomically(tmp_path: Path) -> None:
    (tmp_path / "WESLY.md").write_text("x" * (16 * 1024 + 1), encoding="utf-8")

    with pytest.raises(InstructionLoadError) as raised:
        ReadOnlyContextBuilder(
            tmp_path,
            global_instructions_path=tmp_path / "missing-global.md",
        )

    assert raised.value.error_code == "instructions_limit"
    assert "WESLY.md" in str(raised.value)


def test_instruction_total_limit_includes_global_and_workspace_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    global_file = tmp_path / "global" / "WESLY.md"
    global_file.parent.mkdir()
    global_file.write_text("g" * (12 * 1024), encoding="utf-8")
    (workspace / "WESLY.md").write_text("r" * (12 * 1024), encoding="utf-8")
    (nested / "WESLY.md").write_text("n" * (12 * 1024), encoding="utf-8")

    with pytest.raises(InstructionLoadError) as raised:
        ReadOnlyContextBuilder(
            workspace,
            global_instructions_path=global_file,
        )

    assert raised.value.error_code == "instructions_limit"


def test_instruction_limit_is_checked_before_reading_file_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instructions = tmp_path / "WESLY.md"
    instructions.write_text("x" * (16 * 1024 + 1), encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def reject_read(path: Path) -> bytes:
        if path == instructions:
            raise AssertionError("oversized instruction should not be read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_read)

    with pytest.raises(InstructionLoadError) as raised:
        ReadOnlyContextBuilder(
            tmp_path,
            global_instructions_path=tmp_path / "missing-global.md",
        )

    assert raised.value.error_code == "instructions_limit"
