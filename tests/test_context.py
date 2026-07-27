from collections.abc import Mapping
import hashlib
from pathlib import Path
from typing import cast

import pytest

from wesly.context import (
    DirectAnswerContextBuilder,
    InstructionLoadError,
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
    assert "[[workspace/relative/path]]" in request.instructions[0]


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
