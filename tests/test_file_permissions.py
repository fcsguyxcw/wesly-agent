import base64
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from wesly.model import ToolCall
from wesly.permissions import PreparedOperation
from wesly.tools import ToolRegistry


def operation_call(
    *operations: dict[str, object],
    reason: str = "完成用户要求的文件变更",
) -> ToolCall:
    return ToolCall(
        id="effects",
        name="apply_file_operations",
        arguments_json=json.dumps(
            {"operations": list(operations), "reason": reason},
            ensure_ascii=False,
        ),
    )


def assert_denied_then_allowed(
    registry: ToolRegistry,
    call: ToolCall,
    unchanged: Callable[[], None],
    changed: Callable[[], None],
) -> PreparedOperation:
    prepared = registry.prepare_operation(call)
    assert isinstance(prepared, PreparedOperation)

    denied = registry.execute(call)
    assert denied.status == "error"
    assert denied.error_code == "permission_denied"
    unchanged()

    prepared = registry.prepare_operation(call)
    assert isinstance(prepared, PreparedOperation)
    allowed = registry.execute(call, approved_operation=prepared)
    assert allowed.status == "success"
    changed()
    return prepared


def test_create_text_file_requires_one_time_approval(tmp_path: Path) -> None:
    target = tmp_path / "created.txt"
    registry = ToolRegistry(tmp_path)
    call = operation_call({"kind": "write_text", "path": "created.txt", "content": "new\n"})

    prepared = assert_denied_then_allowed(
        registry,
        call,
        lambda: assert_path_missing(target),
        lambda: assert_text(target, "new\n"),
    )

    assert prepared.operation == "apply_file_operations"
    assert prepared.resolved_targets == (str(target.resolve()),)
    assert prepared.reason == "完成用户要求的文件变更"
    assert prepared.impact_scope == "1 file effect: create_text"
    assert prepared.workspace == str(tmp_path.resolve())
    assert "content_sha256" in prepared.parameters
    assert "new\\n" not in prepared.parameters
    replay = registry.execute(call, approved_operation=prepared)
    assert replay.error_code == "permission_denied"


def test_approval_is_bound_to_the_exact_tool_call(tmp_path: Path) -> None:
    target = tmp_path / "created.txt"
    registry = ToolRegistry(tmp_path)
    original = operation_call(
        {"kind": "write_text", "path": "created.txt", "content": "original"}
    )
    prepared = registry.prepare_operation(original)
    assert isinstance(prepared, PreparedOperation)
    altered = operation_call(
        {"kind": "write_text", "path": "created.txt", "content": "altered"}
    )

    mismatch = registry.execute(altered, approved_operation=prepared)

    assert mismatch.error_code == "permission_denied"
    assert not target.exists()
    allowed = registry.execute(original, approved_operation=prepared)
    assert allowed.status == "success"
    assert_text(target, "original")


def test_delete_file_requires_approval_and_captures_precondition(tmp_path: Path) -> None:
    target = tmp_path / "remove.txt"
    target.write_text("old", encoding="utf-8")
    expected_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    registry = ToolRegistry(tmp_path)
    call = operation_call({"kind": "delete", "path": "remove.txt"})

    prepared = assert_denied_then_allowed(
        registry,
        call,
        lambda: assert_text(target, "old"),
        lambda: assert_path_missing(target),
    )

    assert expected_hash in prepared.parameters


@pytest.mark.parametrize(
    ("destination", "impact"),
    [("renamed.txt", "rename"), ("sub/moved.txt", "move")],
)
def test_move_and_rename_require_approval(
    tmp_path: Path,
    destination: str,
    impact: str,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    destination_path = tmp_path / destination
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "move", "path": "source.txt", "destination": destination}
    )

    prepared = assert_denied_then_allowed(
        registry,
        call,
        lambda: (assert_text(source, "payload"), assert_path_missing(destination_path)),
        lambda: (assert_path_missing(source), assert_text(destination_path, "payload")),
    )

    assert impact in prepared.impact_scope


def test_batch_modification_requires_one_approval_for_exact_batch(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("old-1", encoding="utf-8")
    second.write_text("old-2", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "write_text", "path": "first.txt", "content": "new-1"},
        {"kind": "write_text", "path": "second.txt", "content": "new-2"},
    )

    prepared = assert_denied_then_allowed(
        registry,
        call,
        lambda: (assert_text(first, "old-1"), assert_text(second, "old-2")),
        lambda: (assert_text(first, "new-1"), assert_text(second, "new-2")),
    )

    assert prepared.impact_scope == "2 file effects: overwrite_text, overwrite_text"


def test_binary_write_requires_approval(tmp_path: Path) -> None:
    target = tmp_path / "image.bin"
    target.write_bytes(b"old-binary")
    registry = ToolRegistry(tmp_path)
    encoded = base64.b64encode(b"\x00\xffbinary").decode("ascii")
    call = operation_call(
        {"kind": "write_binary", "path": "image.bin", "content_base64": encoded}
    )

    prepared = assert_denied_then_allowed(
        registry,
        call,
        lambda: assert_bytes(target, b"old-binary"),
        lambda: assert_bytes(target, b"\x00\xffbinary"),
    )

    assert "overwrite_binary" in prepared.impact_scope


def test_sensitive_configuration_write_requires_approval(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("TOKEN=old", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "write_text", "path": ".env", "content": "TOKEN=new"}
    )

    prepared = assert_denied_then_allowed(
        registry,
        call,
        lambda: assert_text(target, "TOKEN=old"),
        lambda: assert_text(target, "TOKEN=new"),
    )

    assert prepared.sensitivity == "sensitive"
    assert "TOKEN=new" not in prepared.parameters


def test_workspace_external_write_requires_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "outside.txt"
    registry = ToolRegistry(workspace)
    call = operation_call(
        {"kind": "write_text", "path": str(target), "content": "outside"}
    )

    prepared = assert_denied_then_allowed(
        registry,
        call,
        lambda: assert_path_missing(target),
        lambda: assert_text(target, "outside"),
    )

    assert prepared.sensitivity == "workspace_external"


def test_external_sensitive_configuration_is_labeled_with_both_risks(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / ".env.external"
    registry = ToolRegistry(workspace)
    call = operation_call(
        {"kind": "write_text", "path": str(target), "content": "TOKEN=new"}
    )

    prepared = registry.prepare_operation(call)

    assert isinstance(prepared, PreparedOperation)
    assert prepared.sensitivity == "sensitive_workspace_external"


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "write_text", "path": "credentials.json", "content": "secret"},
        {"kind": "delete", "path": ".git"},
        {"kind": "delete", "path": "missing.txt"},
        {"kind": "write_text", "path": ".ssh/authorized_keys", "content": "key"},
        {
            "kind": "write_text",
            "path": "same.txt",
            "content": "one",
        },
    ],
)
def test_forbidden_or_unverifiable_operations_cannot_be_approved(
    tmp_path: Path,
    operation: dict[str, object],
) -> None:
    (tmp_path / ".git").mkdir(exist_ok=True)
    (tmp_path / ".ssh").mkdir(exist_ok=True)
    (tmp_path / "same.txt").write_text("old", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    operations = (
        (operation, {"kind": "delete", "path": "same.txt"})
        if operation.get("path") == "same.txt"
        else (operation,)
    )
    call = operation_call(*operations)

    assert registry.prepare_operation(call) is None
    result = registry.execute(call)

    assert result.status == "error"
    assert result.error_code == "permission_denied"


def test_hard_link_target_is_denied_as_unbounded_effect(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    linked = tmp_path / "linked.txt"
    original.write_text("old", encoding="utf-8")
    try:
        os.link(original, linked)
    except OSError as error:
        pytest.skip(f"当前文件系统不允许创建硬链接: {error}")
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "write_text", "path": "linked.txt", "content": "new"}
    )

    assert registry.prepare_operation(call) is None
    result = registry.execute(call)

    assert result.error_code == "permission_denied"
    assert_text(original, "old")


def assert_path_missing(path: Path) -> None:
    assert not path.exists()


def assert_text(path: Path, expected: str) -> None:
    assert path.read_text(encoding="utf-8") == expected


def assert_bytes(path: Path, expected: bytes) -> None:
    assert path.read_bytes() == expected
