import json
from pathlib import Path

import pytest

from wesly.model import ToolCall
from wesly.tools import ToolRegistry


def test_list_workspace_paginates_sorted_entries(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "a").mkdir()
    registry = ToolRegistry(tmp_path)

    result = registry.execute(
        ToolCall(
            id="call-1",
            name="list_workspace",
            arguments_json='{"path":".","limit":1}',
        )
    )

    assert result.status == "success"
    payload = json.loads(result.content)
    assert payload == {
        "path": ".",
        "entries": [{"name": "a", "kind": "directory"}],
        "range": {"start": 0, "end": 1},
        "has_more": True,
        "next_cursor": 1,
    }


def test_list_workspace_rejects_schema_errors(tmp_path: Path) -> None:
    result = ToolRegistry(tmp_path).execute(
        ToolCall(
            id="call-1",
            name="list_workspace",
            arguments_json='{"path":".","unexpected":true}',
        )
    )

    assert result.status == "error"
    assert result.error_code == "invalid_arguments"


def test_list_workspace_reports_a_missing_in_workspace_directory(
    tmp_path: Path,
) -> None:
    result = ToolRegistry(tmp_path).execute(
        ToolCall(
            id="call-1",
            name="list_workspace",
            arguments_json='{"path":"missing"}',
        )
    )

    assert result.status == "error"
    assert result.error_code == "not_found"


def test_list_workspace_rejects_a_symlink_that_escapes_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"当前 Windows 环境不允许创建目录符号链接: {error}")

    result = ToolRegistry(workspace).execute(
        ToolCall(
            id="call-1",
            name="list_workspace",
            arguments_json='{"path":"outside-link"}',
        )
    )

    assert result.status == "error"
    assert result.error_code == "permission_denied"
