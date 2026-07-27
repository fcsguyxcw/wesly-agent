import json
from pathlib import Path

import pytest

import wesly.tools
from wesly.model import ToolCall
from wesly.permissions import PreparedOperation
from wesly.tools import ToolRegistry


def operation_call(operation: dict[str, object]) -> ToolCall:
    return ToolCall(
        "effects",
        "apply_file_operations",
        json.dumps({"reason": "apply exact effect", "operations": [operation]}),
    )


def prepare(registry: ToolRegistry, call: ToolCall) -> PreparedOperation:
    operation = registry.prepare_operation(call)
    assert isinstance(operation, PreparedOperation)
    return operation


def assert_drift(registry: ToolRegistry, call: ToolCall, approved: PreparedOperation) -> None:
    result = registry.execute(call, approved_operation=approved)
    assert result.status == "error"
    assert result.error_code == "operation_drift"
    replay = registry.execute(call, approved_operation=approved)
    assert replay.error_code == "permission_denied"


def test_hash_change_after_approval_does_not_overwrite_user_edit(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("approved baseline", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "write_text", "path": "target.txt", "content": "agent change"}
    )
    approved = prepare(registry, call)
    target.write_text("user change", encoding="utf-8")

    assert_drift(registry, call, approved)

    assert target.read_text(encoding="utf-8") == "user change"


def test_target_type_change_after_approval_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("delete me", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    call = operation_call({"kind": "delete", "path": "target.txt"})
    approved = prepare(registry, call)
    target.unlink()
    target.mkdir()

    assert_drift(registry, call, approved)

    assert target.is_dir()


def test_create_target_appearing_after_approval_is_not_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "write_text", "path": "new.txt", "content": "agent content"}
    )
    approved = prepare(registry, call)
    target.write_text("user content", encoding="utf-8")

    assert_drift(registry, call, approved)

    assert target.read_text(encoding="utf-8") == "user content"


def test_move_destination_appearing_after_approval_is_not_overwritten(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "move", "path": "source.txt", "destination": "destination.txt"}
    )
    approved = prepare(registry, call)
    destination.write_text("user destination", encoding="utf-8")

    assert_drift(registry, call, approved)

    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "user destination"


def test_file_symlink_replacement_after_approval_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("baseline", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    registry = ToolRegistry(workspace)
    call = operation_call(
        {"kind": "write_text", "path": "target.txt", "content": "agent content"}
    )
    approved = prepare(registry, call)
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"当前 Windows 环境不允许创建文件符号链接: {error}")

    assert_drift(registry, call, approved)

    assert outside.read_text(encoding="utf-8") == "outside"


def test_parent_scope_change_after_approval_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent = workspace / "sub"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    registry = ToolRegistry(workspace)
    call = operation_call(
        {"kind": "write_text", "path": "sub/new.txt", "content": "agent content"}
    )
    approved = prepare(registry, call)
    parent.rmdir()
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"当前 Windows 环境不允许创建目录符号链接: {error}")

    assert_drift(registry, call, approved)

    assert not (outside / "new.txt").exists()


def test_hash_is_checked_again_immediately_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("baseline", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "write_text", "path": "target.txt", "content": "agent content"}
    )
    approved = prepare(registry, call)
    original_chmod = wesly.tools.os.chmod

    def drift_during_staging(path: str | Path, mode: int) -> None:
        original_chmod(path, mode)
        target.write_text("user content", encoding="utf-8")

    monkeypatch.setattr(wesly.tools.os, "chmod", drift_during_staging)

    assert_drift(registry, call, approved)

    assert target.read_text(encoding="utf-8") == "user content"
    assert list(tmp_path.glob(".wesly-effect-*")) == []


def test_revalidation_policy_exception_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "new.txt"
    registry = ToolRegistry(tmp_path)
    call = operation_call(
        {"kind": "write_text", "path": "new.txt", "content": "agent content"}
    )
    approved = prepare(registry, call)

    def broken_revalidation(call: ToolCall, arguments: dict[str, object]) -> object:
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(registry, "_prepare_file_operations", broken_revalidation)

    result = registry.execute(call, approved_operation=approved)

    assert result.error_code == "permission_denied"
    assert not target.exists()
