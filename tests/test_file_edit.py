import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

import wesly.tools
from wesly.model import ToolCall
from wesly.tools import FileEditPreview, ToolRegistry


def read_file(registry: ToolRegistry, path: str) -> dict[str, object]:
    result = registry.execute(
        ToolCall("read", "read_file", json.dumps({"path": path}))
    )
    assert result.status == "success"
    return cast(dict[str, object], json.loads(result.content))


def patch_call(path: str, expected_sha256: str) -> ToolCall:
    return ToolCall(
        "patch",
        "apply_patch",
        json.dumps(
            {
                "path": path,
                "expected_sha256": expected_sha256,
                "old_text": "value = 1",
                "new_text": "value = 2",
            }
        ),
    )


def test_read_records_disk_hash_and_successful_patch_preserves_hash_history(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    read_payload = read_file(registry, "sample.py")
    old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    call = patch_call("sample.py", old_hash)
    preview = registry.preview(call)

    assert read_payload["sha256"] == old_hash
    assert isinstance(preview, FileEditPreview)
    assert preview.diff.startswith("--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n")
    assert "-value = 1" in preview.diff
    assert "+value = 2" in preview.diff
    without_preview = registry.execute(call)
    assert without_preview.error_code == "preview_required"
    assert target.read_text(encoding="utf-8") == "value = 1\n"

    result = registry.execute(call, preview=preview)

    new_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.status == "success"
    assert json.loads(result.content) == {
        "path": "sample.py",
        "previous_sha256": old_hash,
        "sha256": new_hash,
    }
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert [(item.source, item.sha256) for item in registry.observation_history] == [
        ("read", old_hash),
        ("write", new_hash),
    ]
    assert registry.latest_observation("sample.py") == registry.observation_history[-1]


def test_patch_rejects_unobserved_and_concurrently_changed_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    old_hash = hashlib.sha256(target.read_bytes()).hexdigest()

    unobserved = registry.execute(patch_call("sample.py", old_hash))
    read_file(registry, "sample.py")
    preview = registry.preview(patch_call("sample.py", old_hash))
    assert isinstance(preview, FileEditPreview)
    target.write_text("value = user\n", encoding="utf-8")
    conflict = registry.execute(patch_call("sample.py", old_hash), preview=preview)

    assert unobserved.error_code == "observation_required"
    assert conflict.error_code == "hash_conflict"
    assert "重新读取" in conflict.content
    assert target.read_text(encoding="utf-8") == "value = user\n"
    assert len(registry.observation_history) == 1


def test_patch_uses_atomic_replace_and_leaves_original_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    old_hash = read_file(registry, "sample.py")["sha256"]
    assert isinstance(old_hash, str)
    call = patch_call("sample.py", old_hash)
    preview = registry.preview(call)
    assert isinstance(preview, FileEditPreview)

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(wesly.tools.os, "replace", fail_replace)
    result = registry.execute(call, preview=preview)

    assert result.error_code == "write_failed"
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert list(tmp_path.glob(".wesly-edit-*")) == []
    assert len(registry.observation_history) == 1


def test_patch_reports_changed_path_when_post_replace_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    old_hash = read_file(registry, "sample.py")["sha256"]
    assert isinstance(old_hash, str)
    call = patch_call("sample.py", old_hash)
    preview = registry.preview(call)
    assert isinstance(preview, FileEditPreview)
    original_read_bytes = Path.read_bytes
    reads = 0

    def fail_post_replace_read(path: Path) -> bytes:
        nonlocal reads
        if path == target:
            reads += 1
            if reads == 3:
                raise OSError("simulated post-replace read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_post_replace_read)

    result = registry.execute(call, preview=preview)

    assert result.error_code == "write_failed"
    assert result.changed_paths == ("sample.py",)
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_patch_rechecks_hash_immediately_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    old_hash = read_file(registry, "sample.py")["sha256"]
    assert isinstance(old_hash, str)
    call = patch_call("sample.py", old_hash)
    preview = registry.preview(call)
    assert isinstance(preview, FileEditPreview)
    original_chmod = wesly.tools.os.chmod

    def change_target_during_staging(path: str | Path, mode: int) -> None:
        original_chmod(path, mode)
        target.write_text("value = user\n", encoding="utf-8")

    monkeypatch.setattr(wesly.tools.os, "chmod", change_target_during_staging)
    result = registry.execute(call, preview=preview)

    assert result.error_code == "hash_conflict"
    assert target.read_text(encoding="utf-8") == "value = user\n"
    assert list(tmp_path.glob(".wesly-edit-*")) == []


def test_patch_rejects_sensitive_missing_and_ambiguous_targets(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("value = 1\n", encoding="utf-8")
    repeated = tmp_path / "repeated.py"
    repeated.write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    repeated_hash = read_file(registry, "repeated.py")["sha256"]
    assert isinstance(repeated_hash, str)

    sensitive = registry.execute(patch_call(".env", "0" * 64))
    missing = registry.execute(patch_call("missing.py", "0" * 64))
    ambiguous = registry.execute(patch_call("repeated.py", repeated_hash))

    assert sensitive.error_code == "permission_denied"
    assert missing.error_code == "not_found"
    assert ambiguous.error_code == "patch_conflict"
