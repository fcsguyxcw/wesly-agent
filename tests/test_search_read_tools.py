import json
from pathlib import Path
from typing import cast

import pytest

from wesly.model import ToolCall
from wesly.tools import ToolRegistry


def execute(registry: ToolRegistry, name: str, arguments: str) -> dict[str, object]:
    result = registry.execute(ToolCall("call-1", name, arguments))
    assert result.status == "success"
    return cast(dict[str, object], json.loads(result.content))


def test_search_text_returns_explicit_pages_from_real_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("needle one\nnone\nneedle two\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle three\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    first = execute(
        registry,
        "search_text",
        '{"query":"needle","path":".","limit":2}',
    )
    second = execute(
        registry,
        "search_text",
        '{"query":"needle","path":".","cursor":2,"limit":2}',
    )

    assert first == {
        "query": "needle",
        "path": ".",
        "matches": [
            {"path": "a.py", "line": 1, "column": 1, "preview": "needle one"},
            {"path": "a.py", "line": 3, "column": 1, "preview": "needle two"},
        ],
        "range": {"start": 0, "end": 2},
        "truncated": True,
        "next_cursor": 2,
        "warnings": [],
    }
    assert second["matches"] == [
        {"path": "b.py", "line": 1, "column": 1, "preview": "needle three"}
    ]
    assert second["range"] == {"start": 2, "end": 3}
    assert second["truncated"] is False
    assert second["next_cursor"] is None


def test_read_file_returns_line_ranges_and_next_cursor(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    page = execute(
        registry,
        "read_file",
        '{"path":"sample.py","cursor":1,"limit":1}',
    )

    assert page == {
        "path": "sample.py",
        "lines": [{"number": 2, "text": "two"}],
        "range": {"start": 1, "end": 2},
        "total_lines": 3,
        "truncated": True,
        "next_cursor": 2,
    }


def test_read_file_reports_missing_decode_and_read_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "bad.bin").write_bytes(b"\xff")
    blocked = tmp_path / "blocked.txt"
    blocked.write_text("secret", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    missing = registry.execute(
        ToolCall("missing", "read_file", '{"path":"missing.txt"}')
    )
    undecodable = registry.execute(
        ToolCall("decode", "read_file", '{"path":"bad.bin"}')
    )
    original_read_text = Path.read_text

    def fail_blocked(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == blocked:
            raise PermissionError("blocked")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", fail_blocked)
    unreadable = registry.execute(
        ToolCall("read", "read_file", '{"path":"blocked.txt"}')
    )

    assert missing.error_code == "not_found"
    assert undecodable.error_code == "decode_failed"
    assert unreadable.error_code == "read_failed"


@pytest.mark.parametrize("tool_name", ["search_text", "read_file"])
def test_search_and_read_reject_path_escape(tmp_path: Path, tool_name: str) -> None:
    arguments = '{"query":"x","path":".."}' if tool_name == "search_text" else '{"path":".."}'

    result = ToolRegistry(tmp_path).execute(
        ToolCall("escape", tool_name, arguments)
    )

    assert result.status == "error"
    assert result.error_code == "permission_denied"


def test_oversized_read_page_is_an_explicit_error(tmp_path: Path) -> None:
    (tmp_path / "huge.txt").write_text("x" * 40_000, encoding="utf-8")

    result = ToolRegistry(tmp_path).execute(
        ToolCall("large", "read_file", '{"path":"huge.txt","limit":1}')
    )

    assert result.status == "error"
    assert result.error_code == "tool_result_too_large"


def test_search_and_read_do_not_expose_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("needle=secret", encoding="utf-8")
    (tmp_path / ".env.local").write_text("needle=local-secret", encoding="utf-8")
    (tmp_path / "safe.py").write_text("needle = 'public'", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    search = registry.execute(
        ToolCall("search", "search_text", '{"query":"needle"}')
    )
    read = registry.execute(
        ToolCall("read", "read_file", '{"path":".env"}')
    )

    payload = json.loads(search.content)
    assert [match["path"] for match in payload["matches"]] == ["safe.py"]
    assert payload["warnings"] == [
        {"code": "permission_denied", "path": ".env"},
        {"code": "permission_denied", "path": ".env.local"},
    ]
    assert "secret" not in search.content
    assert read.status == "error"
    assert read.error_code == "permission_denied"
