from __future__ import annotations

import difflib
import base64
import binascii
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from wesly.model import ToolCall, ToolResult
from wesly.permissions import (
    FileEffectKind,
    NormalizedFileEffect,
    PreparedOperation,
    Sensitivity,
)


MAX_TOOL_RESULT_BYTES = 32 * 1024
MAX_EDIT_FILE_BYTES = 1024 * 1024
MAX_PATCH_FRAGMENT_BYTES = 32 * 1024
MAX_HIGH_RISK_CONTENT_BYTES = 1024 * 1024
MAX_FILE_OPERATIONS = 20
MAX_OPERATION_REASON_BYTES = 2 * 1024
SKIPPED_SEARCH_DIRECTORIES = frozenset(
    {
        ".aws",
        ".azure",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ssh",
        ".uv-cache",
        ".venv",
        "__pycache__",
    }
)
SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
    }
)
SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pfx", ".pem"})
WINDOWS_RESERVED_NAMES = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
FORBIDDEN_CREDENTIAL_NAMES = frozenset(
    {
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
    }
)
FORBIDDEN_CREDENTIAL_DIRECTORIES = frozenset({".aws", ".azure", ".ssh"})

TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    {
        "type": "function",
        "function": {
            "name": "list_workspace",
            "description": (
                "List one directory inside the authorized workspace. If truncated, "
                "pass the returned next_cursor to continue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "cursor": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": (
                "Search UTF-8 text inside one workspace file or directory. If "
                "truncated, pass the returned next_cursor to continue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "cursor": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a page of lines from one UTF-8 workspace file. If truncated, "
                "pass the returned next_cursor to continue instead of rereading it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "cursor": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": (
                "Replace one exact, unique text fragment in an existing UTF-8 workspace "
                "file that was read earlier in this run. Pass the latest sha256 returned "
                "by read_file. The diff is shown before an atomic write."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "expected_sha256", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_file_operations",
            "description": (
                "Request one-time user approval for high-risk file effects: create or "
                "overwrite text, write binary bytes, delete a file, move or rename a "
                "file, modify multiple files, or touch sensitive or external targets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_FILE_OPERATIONS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "write_text",
                                        "write_binary",
                                        "delete",
                                        "move",
                                    ],
                                },
                                "path": {"type": "string"},
                                "destination": {"type": "string"},
                                "content": {"type": "string"},
                                "content_base64": {"type": "string"},
                            },
                            "required": ["kind", "path"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["reason", "operations"],
                "additionalProperties": False,
            },
        },
    },
)

@dataclass(frozen=True, slots=True)
class FileObservation:
    path: str
    sha256: str
    source: Literal["read", "write"]


@dataclass(frozen=True, slots=True)
class FileEditPreview:
    call_id: str
    arguments_json: str
    path: str
    target: Path
    previous_sha256: str
    updated_bytes: bytes
    diff: str


class ToolRegistry:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve(strict=True)
        self._observations: list[FileObservation] = []
        self._latest_observations: dict[str, FileObservation] = {}
        self._pending_approvals: dict[str, PreparedOperation] = {}

    @property
    def observation_history(self) -> tuple[FileObservation, ...]:
        return tuple(self._observations)

    def latest_observation(self, path: str) -> FileObservation | None:
        return self._latest_observations.get(Path(path).as_posix())

    def describe_target(self, call: ToolCall) -> str:
        try:
            arguments = json.loads(call.arguments_json)
        except (json.JSONDecodeError, TypeError):
            return "<invalid>"
        if not isinstance(arguments, dict):
            return "<invalid>"
        path = arguments.get("path", ".")
        return path if isinstance(path, str) else "<invalid>"

    def preview(self, call: ToolCall) -> FileEditPreview | None:
        if call.name != "apply_patch":
            return None
        arguments, error = self._parsed_arguments(call)
        if error is not None or arguments is None:
            return None
        prepared = self._prepare_file_edit(call, arguments)
        return prepared if isinstance(prepared, FileEditPreview) else None

    def prepare_operation(self, call: ToolCall) -> PreparedOperation | None:
        if call.name != "apply_file_operations":
            return None
        arguments, error = self._parsed_arguments(call)
        if error is not None or arguments is None:
            return None
        prepared = self._prepare_file_operations(call, arguments)
        if not isinstance(prepared, PreparedOperation):
            return None
        self._pending_approvals[prepared.fingerprint] = prepared
        return prepared

    def revoke_operation(self, operation: PreparedOperation) -> None:
        if self._pending_approvals.get(operation.fingerprint) is operation:
            self._pending_approvals.pop(operation.fingerprint)

    def execute(
        self,
        call: ToolCall,
        *,
        preview: FileEditPreview | None = None,
        approved_operation: PreparedOperation | None = None,
    ) -> ToolResult:
        if call.name not in {
            "list_workspace",
            "search_text",
            "read_file",
            "apply_patch",
            "apply_file_operations",
        }:
            return self._error(call, "unknown_tool", "未知工具", "<unknown>")
        arguments, error = self._parsed_arguments(call)
        if error is not None:
            return error
        assert arguments is not None

        if call.name == "apply_patch":
            if preview is not None and (
                preview.call_id != call.id or preview.arguments_json != call.arguments_json
            ):
                return self._error(call, "invalid_preview", "补丁预览与当前请求不匹配")
            prepared: FileEditPreview | ToolResult = preview or self._prepare_file_edit(
                call, arguments
            )
            if isinstance(prepared, ToolResult):
                return prepared
            if preview is None:
                return self._error(
                    call,
                    "preview_required",
                    "补丁必须先展示差异，再允许执行",
                )
            return self._apply_file_edit(call, prepared)

        if call.name == "apply_file_operations":
            prepared_effects = approved_operation or self._prepare_file_operations(
                call, arguments
            )
            if isinstance(prepared_effects, ToolResult):
                return prepared_effects
            if approved_operation is None:
                self._pending_approvals.pop(prepared_effects.fingerprint, None)
                return self._error(
                    call,
                    "permission_denied",
                    "高风险文件操作需要用户本次明确允许",
                )
            if (
                approved_operation.call_id != call.id
                or approved_operation.arguments_json != call.arguments_json
                or approved_operation.operation != call.name
                or self._pending_approvals.get(approved_operation.fingerprint)
                is not approved_operation
            ):
                return self._error(
                    call,
                    "permission_denied",
                    "批准与当前规范化操作不匹配",
                )
            revalidated = self._revalidate_approved_operation(
                call,
                arguments,
                approved_operation,
            )
            if isinstance(revalidated, ToolResult):
                return revalidated
            self._pending_approvals.pop(approved_operation.fingerprint)
            return self._execute_file_operations(call, revalidated)

        if call.name == "list_workspace":
            return self._list_workspace(call, arguments)
        if call.name == "search_text":
            return self._search_text(call, arguments)
        return self._read_file(call, arguments)

    def _revalidate_approved_operation(
        self,
        call: ToolCall,
        arguments: dict[str, Any],
        approved_operation: PreparedOperation,
    ) -> PreparedOperation | ToolResult:
        try:
            current = self._prepare_file_operations(call, arguments)
        except Exception:
            self._pending_approvals.pop(approved_operation.fingerprint, None)
            return self._error(
                call,
                "permission_denied",
                "执行前权限策略异常；操作未执行",
            )
        if (
            not isinstance(current, PreparedOperation)
            or current.fingerprint != approved_operation.fingerprint
            or current.resolved_targets != approved_operation.resolved_targets
            or current.sensitivity != approved_operation.sensitivity
            or current.effects != approved_operation.effects
        ):
            self._pending_approvals.pop(approved_operation.fingerprint, None)
            return self._error(
                call,
                "operation_drift",
                "批准后的路径、类型、哈希、作用域或效果已变化；请重新调查并审批",
            )
        return current

    def _prepare_file_operations(
        self,
        call: ToolCall,
        arguments: dict[str, Any],
    ) -> PreparedOperation | ToolResult:
        reason = arguments["reason"]
        raw_operations = arguments["operations"]
        assert isinstance(reason, str)
        assert isinstance(raw_operations, list)
        if len(raw_operations) > 1 and any(
            operation["kind"] not in {"write_text", "write_binary"}
            for operation in raw_operations
        ):
            return self._error(
                call,
                "permission_denied",
                "包含删除或移动的批量操作无法安全协调，默认拒绝",
            )

        effects: list[NormalizedFileEffect] = []
        occupied_paths: set[Path] = set()
        for raw_operation in raw_operations:
            assert isinstance(raw_operation, dict)
            normalized = self._normalize_file_effect(call, raw_operation)
            if isinstance(normalized, ToolResult):
                return normalized
            effect_paths = {normalized.target}
            if normalized.destination is not None:
                effect_paths.add(normalized.destination)
            if occupied_paths.intersection(effect_paths):
                return self._error(
                    call,
                    "permission_denied",
                    "批量操作包含重叠目标，无法可靠规范化",
                )
            occupied_paths.update(effect_paths)
            effects.append(normalized)

        display_items = []
        resolved_targets: list[str] = []
        for effect in effects:
            item: dict[str, object] = {
                "kind": effect.kind,
                "path": str(effect.target),
                "effect": effect.effect,
            }
            resolved_targets.append(str(effect.target))
            if effect.destination is not None:
                item["destination"] = str(effect.destination)
                resolved_targets.append(str(effect.destination))
            if effect.content is not None:
                item["content_bytes"] = len(effect.content)
                item["content_sha256"] = hashlib.sha256(effect.content).hexdigest()
            if effect.previous_sha256 is not None:
                item["previous_sha256"] = effect.previous_sha256
            display_items.append(item)
        parameters = json.dumps(
            {"operations": display_items},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint_payload = json.dumps(
            {
                "operation": call.name,
                "reason": reason,
                "workspace": str(self._workspace),
                "parameters": parameters,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        has_external = any(
            effect.sensitivity in {
                "workspace_external",
                "sensitive_workspace_external",
            }
            for effect in effects
        )
        has_sensitive = any(
            effect.sensitivity in {"sensitive", "sensitive_workspace_external"}
            for effect in effects
        )
        sensitivity: Sensitivity
        if has_external and has_sensitive:
            sensitivity = "sensitive_workspace_external"
        elif has_external:
            sensitivity = "workspace_external"
        elif has_sensitive:
            sensitivity = "sensitive"
        else:
            sensitivity = "normal"
        effect_labels = ", ".join(effect.effect for effect in effects)
        unit = "file effect" if len(effects) == 1 else "file effects"
        return PreparedOperation(
            call_id=call.id,
            arguments_json=call.arguments_json,
            fingerprint=hashlib.sha256(fingerprint_payload).hexdigest(),
            operation=call.name,
            parameters=parameters,
            resolved_targets=tuple(resolved_targets),
            reason=reason,
            impact_scope=f"{len(effects)} {unit}: {effect_labels}",
            workspace=str(self._workspace),
            sensitivity=sensitivity,
            effects=tuple(effects),
        )

    def _normalize_file_effect(
        self,
        call: ToolCall,
        operation: dict[str, Any],
    ) -> NormalizedFileEffect | ToolResult:
        kind = cast(FileEffectKind, operation["kind"])
        requested_path = operation["path"]
        assert isinstance(kind, str) and isinstance(requested_path, str)
        unresolved = Path(requested_path)
        if not unresolved.is_absolute():
            unresolved = self._workspace / unresolved
        if unresolved.is_symlink():
            return self._error(call, "permission_denied", "特殊文件或链接默认拒绝")
        try:
            target = unresolved.resolve(strict=False)
        except (OSError, RuntimeError):
            return self._error(call, "permission_denied", "目标无法规范化")
        sensitivity = self._high_risk_sensitivity(target)
        if sensitivity is None:
            return self._error(call, "permission_denied", "凭据位置或特殊目标默认拒绝")

        destination: Path | None = None
        content: bytes | None = None
        previous_sha256: str | None = None
        if target.exists():
            if not target.is_file():
                return self._error(call, "permission_denied", "只允许可验证的普通文件效果")
            try:
                if target.stat().st_nlink != 1:
                    return self._error(
                        call,
                        "permission_denied",
                        "硬链接目标无法安全识别其完整影响范围",
                    )
                previous_sha256 = self._hash_file(target)
            except OSError:
                return self._error(call, "permission_denied", "无法读取目标前置状态")
        elif kind in {"delete", "move"}:
            return self._error(call, "permission_denied", "删除或移动目标不存在")
        elif not target.parent.is_dir():
            return self._error(call, "permission_denied", "目标父目录不存在或不是普通目录")

        if kind == "write_text":
            raw_content = operation["content"]
            assert isinstance(raw_content, str)
            content = raw_content.encode("utf-8")
            effect = "overwrite_text" if previous_sha256 is not None else "create_text"
        elif kind == "write_binary":
            encoded_content = operation["content_base64"]
            assert isinstance(encoded_content, str)
            content = base64.b64decode(encoded_content, validate=True)
            effect = "overwrite_binary" if previous_sha256 is not None else "create_binary"
        elif kind == "delete":
            effect = "delete"
        else:
            requested_destination = operation["destination"]
            assert isinstance(requested_destination, str)
            unresolved_destination = Path(requested_destination)
            if not unresolved_destination.is_absolute():
                unresolved_destination = self._workspace / unresolved_destination
            if unresolved_destination.is_symlink():
                return self._error(call, "permission_denied", "目标链接默认拒绝")
            try:
                destination = unresolved_destination.resolve(strict=False)
            except (OSError, RuntimeError):
                return self._error(call, "permission_denied", "移动目标无法规范化")
            destination_sensitivity = self._high_risk_sensitivity(destination)
            if destination_sensitivity is None:
                return self._error(call, "permission_denied", "移动目标属于禁止位置")
            if destination.exists() or not destination.parent.is_dir():
                return self._error(
                    call,
                    "permission_denied",
                    "移动目标必须不存在且父目录必须已存在",
                )
            sensitivity = self._combine_sensitivity(
                sensitivity,
                destination_sensitivity,
            )
            effect = "rename" if target.parent == destination.parent else "move"

        return NormalizedFileEffect(
            kind=kind,
            requested_path=requested_path,
            target=target,
            requested_destination=(
                requested_destination if kind == "move" else None
            ),
            destination=destination,
            content=content,
            effect=effect,
            sensitivity=sensitivity,
            previous_sha256=previous_sha256,
        )

    def _high_risk_sensitivity(self, target: Path) -> Sensitivity | None:
        parts = tuple(part.casefold() for part in target.parts)
        for part in parts:
            stem = part.split(".", maxsplit=1)[0]
            if (
                part in FORBIDDEN_CREDENTIAL_NAMES
                or part in FORBIDDEN_CREDENTIAL_DIRECTORIES
                or part == ".git"
                or Path(part).suffix in SENSITIVE_SUFFIXES
                or stem in WINDOWS_RESERVED_NAMES
            ):
                return None
        sensitive = any(part == ".env" or part.startswith(".env.") for part in parts)
        try:
            target.relative_to(self._workspace)
        except ValueError:
            return "sensitive_workspace_external" if sensitive else "workspace_external"
        return "sensitive" if sensitive else "normal"

    @staticmethod
    def _combine_sensitivity(
        first: Sensitivity,
        second: Sensitivity,
    ) -> Sensitivity:
        external = {
            "workspace_external",
            "sensitive_workspace_external",
        }
        sensitive = {"sensitive", "sensitive_workspace_external"}
        has_external = first in external or second in external
        has_sensitive = first in sensitive or second in sensitive
        if has_external and has_sensitive:
            return "sensitive_workspace_external"
        if has_external:
            return "workspace_external"
        if has_sensitive:
            return "sensitive"
        return "normal"

    def _execute_file_operations(
        self,
        call: ToolCall,
        operation: PreparedOperation,
    ) -> ToolResult:
        results: list[dict[str, object]] = []
        try:
            for effect in operation.effects:
                if not self._file_effect_still_matches(call, effect):
                    return self._error(
                        call,
                        "operation_drift",
                        "文件效果在执行前再次发生漂移；后续效果未执行",
                    )
                if effect.kind in {"write_text", "write_binary"}:
                    assert effect.content is not None
                    if not self._atomic_write_bytes(call, effect):
                        return self._error(
                            call,
                            "operation_drift",
                            "文件效果在原子替换前发生漂移；后续效果未执行",
                        )
                    content_hash = self._hash_file(effect.target)
                    relative = self._relative_path(effect.target)
                    if relative != "<outside-workspace>":
                        self._record_observation(relative, content_hash, "write")
                    results.append(
                        {
                            "effect": effect.effect,
                            "target": str(effect.target),
                            "sha256": content_hash,
                        }
                    )
                elif effect.kind == "delete":
                    effect.target.unlink()
                    results.append({"effect": effect.effect, "target": str(effect.target)})
                else:
                    assert effect.destination is not None
                    os.replace(effect.target, effect.destination)
                    content_hash = self._hash_file(effect.destination)
                    relative = self._relative_path(effect.destination)
                    if relative != "<outside-workspace>":
                        self._record_observation(relative, content_hash, "write")
                    results.append(
                        {
                            "effect": effect.effect,
                            "target": str(effect.target),
                            "destination": str(effect.destination),
                            "sha256": content_hash,
                        }
                    )
        except OSError:
            return self._error(
                call,
                "operation_failed",
                "已批准的文件操作执行失败；后续效果未执行",
            )
        return self._success(
            call,
            operation.impact_scope,
            {"fingerprint": operation.fingerprint, "effects": results},
        )

    def _file_effect_still_matches(
        self,
        call: ToolCall,
        effect: NormalizedFileEffect,
    ) -> bool:
        raw: dict[str, object] = {
            "kind": effect.kind,
            "path": effect.requested_path,
        }
        if effect.kind == "write_text":
            assert effect.content is not None
            raw["content"] = effect.content.decode("utf-8")
        elif effect.kind == "write_binary":
            assert effect.content is not None
            raw["content_base64"] = base64.b64encode(effect.content).decode("ascii")
        elif effect.kind == "move":
            assert effect.requested_destination is not None
            raw["destination"] = effect.requested_destination
        current = self._normalize_file_effect(call, raw)
        return isinstance(current, NormalizedFileEffect) and current == effect

    def _atomic_write_bytes(
        self,
        call: ToolCall,
        effect: NormalizedFileEffect,
    ) -> bool:
        assert effect.content is not None
        target = effect.target
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=".wesly-effect-",
                delete=False,
            ) as temporary:
                temporary.write(effect.content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            if target.exists():
                os.chmod(temporary_path, target.stat().st_mode)
            if not self._file_effect_still_matches(call, effect):
                return False
            os.replace(temporary_path, target)
            temporary_path = None
            return True
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _hash_file(target: Path) -> str:
        with target.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def _parsed_arguments(
        self,
        call: ToolCall,
    ) -> tuple[dict[str, Any] | None, ToolResult | None]:
        try:
            arguments = json.loads(call.arguments_json)
        except (json.JSONDecodeError, TypeError):
            return None, self._error(call, "invalid_json", "工具参数不是有效 JSON")

        validation_error = self._validate_arguments(call.name, arguments)
        if validation_error is not None:
            return None, self._error(call, "invalid_arguments", validation_error)
        assert isinstance(arguments, dict)
        return arguments, None

    def _list_workspace(
        self,
        call: ToolCall,
        arguments: dict[str, Any],
    ) -> ToolResult:
        requested_path = arguments.get("path", ".")
        assert isinstance(requested_path, str)
        target = self._authorized_target(requested_path)
        error = self._target_error(call, requested_path, target)
        if error is not None:
            return error
        assert target is not None
        if not target.is_dir():
            return self._error(call, "not_a_directory", "目标不是可读取目录", requested_path)

        cursor = arguments.get("cursor", 0)
        limit = arguments.get("limit", 100)
        assert isinstance(cursor, int) and isinstance(limit, int)
        try:
            children = sorted(target.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            return self._error(call, "read_failed", "目录读取失败", requested_path)
        selected = children[cursor : cursor + limit]
        end = cursor + len(selected)
        truncated = end < len(children)
        return self._success(
            call,
            requested_path,
            {
                "path": self._relative_path(target),
                "entries": [
                    {
                        "name": child.name,
                        "kind": "directory" if child.is_dir() else "file",
                    }
                    for child in selected
                ],
                "range": {"start": cursor, "end": end},
                "truncated": truncated,
                "has_more": truncated,
                "next_cursor": end if truncated else None,
            },
        )

    def _search_text(
        self,
        call: ToolCall,
        arguments: dict[str, Any],
    ) -> ToolResult:
        query = arguments["query"]
        requested_path = arguments.get("path", ".")
        cursor = arguments.get("cursor", 0)
        limit = arguments.get("limit", 50)
        assert isinstance(query, str)
        assert isinstance(requested_path, str)
        assert isinstance(cursor, int) and isinstance(limit, int)
        target = self._authorized_target(requested_path)
        error = self._target_error(call, requested_path, target)
        if error is not None:
            return error
        assert target is not None

        warnings: list[dict[str, str]] = []
        matches: list[dict[str, object]] = []
        matched_paths: list[str] = []
        seen = 0
        truncated = False
        for match in self._iter_matches(target, query, warnings):
            if seen < cursor:
                seen += 1
                continue
            if len(matches) == limit:
                truncated = True
                break
            matches.append(match)
            path = match["path"]
            assert isinstance(path, str)
            if path not in matched_paths:
                matched_paths.append(path)
            seen += 1
        end = cursor + len(matches)
        return self._success(
            call,
            requested_path,
            {
                "query": query,
                "path": self._relative_path(target),
                "matches": matches,
                "range": {"start": cursor, "end": end},
                "truncated": truncated,
                "next_cursor": end if truncated else None,
                "warnings": warnings,
            },
            evidence_paths=tuple(matched_paths),
        )

    def _read_file(
        self,
        call: ToolCall,
        arguments: dict[str, Any],
    ) -> ToolResult:
        requested_path = arguments["path"]
        cursor = arguments.get("cursor", 0)
        limit = arguments.get("limit", 200)
        assert isinstance(requested_path, str)
        assert isinstance(cursor, int) and isinstance(limit, int)
        target = self._authorized_target(requested_path)
        error = self._target_error(call, requested_path, target)
        if error is not None:
            return error
        assert target is not None
        if not target.is_file():
            return self._error(call, "not_a_file", "目标不是普通文件", requested_path)
        try:
            data = target.read_bytes()
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return self._error(call, "decode_failed", "文件不是有效 UTF-8 文本", requested_path)
        except OSError:
            return self._error(call, "read_failed", "文件读取失败", requested_path)

        lines = text.splitlines()
        selected = lines[cursor : cursor + limit]
        end = cursor + len(selected)
        truncated = end < len(lines)
        relative_path = self._relative_path(target)
        content_hash = hashlib.sha256(data).hexdigest()
        result = self._success(
            call,
            requested_path,
            {
                "path": relative_path,
                "sha256": content_hash,
                "lines": [
                    {"number": cursor + offset + 1, "text": line}
                    for offset, line in enumerate(selected)
                ],
                "range": {"start": cursor, "end": end},
                "total_lines": len(lines),
                "truncated": truncated,
                "next_cursor": end if truncated else None,
            },
            evidence_paths=(relative_path,),
        )
        if result.status == "success":
            self._record_observation(relative_path, content_hash, "read")
        return result

    def _prepare_file_edit(
        self,
        call: ToolCall,
        arguments: dict[str, Any],
    ) -> FileEditPreview | ToolResult:
        requested_path = arguments["path"]
        expected_sha256 = arguments["expected_sha256"].lower()
        old_text = arguments["old_text"]
        new_text = arguments["new_text"]
        assert isinstance(requested_path, str)
        assert isinstance(expected_sha256, str)
        assert isinstance(old_text, str) and isinstance(new_text, str)

        target = self._authorized_target(requested_path)
        error = self._target_error(call, requested_path, target)
        if error is not None:
            return error
        assert target is not None
        if not target.is_file():
            return self._error(call, "not_a_file", "目标不是普通文件", requested_path)
        relative_path = self._relative_path(target)
        latest = self._latest_observations.get(relative_path)
        if latest is None:
            return self._error(
                call,
                "observation_required",
                "修改前必须先重新读取目标文件",
                requested_path,
            )
        if latest.sha256 != expected_sha256:
            return self._error(
                call,
                "hash_conflict",
                "版本前置条件已过期，请重新读取目标文件后再生成补丁",
                requested_path,
            )
        try:
            current_bytes = target.read_bytes()
            current_text = current_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return self._error(call, "decode_failed", "文件不是有效 UTF-8 文本", requested_path)
        except OSError:
            return self._error(call, "read_failed", "文件读取失败", requested_path)
        if len(current_bytes) > MAX_EDIT_FILE_BYTES:
            return self._error(call, "edit_too_large", "目标文件超过单文件修改上限", requested_path)
        current_sha256 = hashlib.sha256(current_bytes).hexdigest()
        if current_sha256 != expected_sha256:
            return self._error(
                call,
                "hash_conflict",
                "磁盘文件已在观察后发生变化，请重新读取目标文件后再生成补丁",
                requested_path,
            )
        if current_text.count(old_text) != 1:
            return self._error(
                call,
                "patch_conflict",
                "old_text 必须在目标文件中精确出现一次，请重新调查并缩小补丁范围",
                requested_path,
            )
        updated_text = current_text.replace(old_text, new_text, 1)
        updated_bytes = updated_text.encode("utf-8")
        if len(updated_bytes) > MAX_EDIT_FILE_BYTES:
            return self._error(call, "edit_too_large", "修改后文件超过大小上限", requested_path)
        diff = "".join(
            difflib.unified_diff(
                current_text.splitlines(keepends=True),
                updated_text.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                lineterm="\n",
            )
        )
        return FileEditPreview(
            call_id=call.id,
            arguments_json=call.arguments_json,
            path=relative_path,
            target=target,
            previous_sha256=current_sha256,
            updated_bytes=updated_bytes,
            diff=diff,
        )

    def _apply_file_edit(
        self,
        call: ToolCall,
        preview: FileEditPreview,
    ) -> ToolResult:
        latest = self._latest_observations.get(preview.path)
        if latest is None or latest.sha256 != preview.previous_sha256:
            return self._error(
                call,
                "hash_conflict",
                "版本前置条件已过期，请重新读取目标文件后再生成补丁",
                preview.path,
            )
        try:
            current_bytes = preview.target.read_bytes()
        except OSError:
            return self._error(call, "read_failed", "文件读取失败", preview.path)
        if hashlib.sha256(current_bytes).hexdigest() != preview.previous_sha256:
            return self._error(
                call,
                "hash_conflict",
                "磁盘文件已在补丁预览后发生变化，请重新读取目标文件后再生成补丁",
                preview.path,
            )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=preview.target.parent,
                prefix=".wesly-edit-",
                delete=False,
            ) as temporary:
                temporary.write(preview.updated_bytes)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.chmod(temporary_path, preview.target.stat().st_mode)
            latest_bytes = preview.target.read_bytes()
            if hashlib.sha256(latest_bytes).hexdigest() != preview.previous_sha256:
                temporary_path.unlink()
                temporary_path = None
                return self._error(
                    call,
                    "hash_conflict",
                    "磁盘文件在原子替换前发生变化，请重新读取目标文件后再生成补丁",
                    preview.path,
                )
            os.replace(temporary_path, preview.target)
            temporary_path = None
            verified_bytes = preview.target.read_bytes()
        except OSError:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return self._error(call, "write_failed", "文件原子替换失败", preview.path)

        updated_sha256 = hashlib.sha256(verified_bytes).hexdigest()
        expected_updated_sha256 = hashlib.sha256(preview.updated_bytes).hexdigest()
        if updated_sha256 != expected_updated_sha256:
            return self._error(
                call,
                "write_verification_failed",
                "写入后的磁盘哈希与补丁结果不一致",
                preview.path,
            )
        self._record_observation(preview.path, updated_sha256, "write")
        return self._success(
            call,
            preview.path,
            {
                "path": preview.path,
                "previous_sha256": preview.previous_sha256,
                "sha256": updated_sha256,
            },
        )

    def _record_observation(
        self,
        path: str,
        sha256: str,
        source: Literal["read", "write"],
    ) -> None:
        observation = FileObservation(path=path, sha256=sha256, source=source)
        self._observations.append(observation)
        self._latest_observations[path] = observation

    def _iter_matches(
        self,
        target: Path,
        query: str,
        warnings: list[dict[str, str]],
    ) -> Iterator[dict[str, object]]:
        files = (target,) if target.is_file() else self._iter_files(target, warnings)
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                warnings.append({"code": "decode_failed", "path": self._relative_path(path)})
                continue
            except OSError:
                warnings.append({"code": "read_failed", "path": self._relative_path(path)})
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                start = 0
                while True:
                    column = line.find(query, start)
                    if column < 0:
                        break
                    yield {
                        "path": self._relative_path(path),
                        "line": line_number,
                        "column": column + 1,
                        "preview": line[:240],
                    }
                    start = column + max(len(query), 1)

    def _iter_files(
        self,
        root: Path,
        warnings: list[dict[str, str]],
    ) -> Iterator[Path]:
        pending = [root]
        seen_directories: set[Path] = set()
        files: list[Path] = []
        while pending:
            directory = pending.pop()
            try:
                resolved_directory = directory.resolve(strict=True)
                resolved_directory.relative_to(self._workspace)
            except (OSError, RuntimeError, ValueError):
                warnings.append({"code": "permission_denied", "path": self._relative_path(directory)})
                continue
            if resolved_directory in seen_directories:
                continue
            seen_directories.add(resolved_directory)
            try:
                children = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
            except OSError:
                warnings.append({"code": "read_failed", "path": self._relative_path(directory)})
                continue
            for child in children:
                if child.is_dir():
                    if child.name.casefold() not in SKIPPED_SEARCH_DIRECTORIES:
                        pending.append(child)
                    continue
                try:
                    resolved = child.resolve(strict=True)
                    resolved.relative_to(self._workspace)
                except (OSError, RuntimeError, ValueError):
                    warnings.append({"code": "permission_denied", "path": self._relative_path(child)})
                    continue
                if self._is_sensitive_target(resolved):
                    warnings.append(
                        {"code": "permission_denied", "path": self._relative_path(resolved)}
                    )
                    continue
                if resolved.is_file():
                    files.append(resolved)
        yield from sorted(files, key=lambda path: self._relative_path(path).casefold())

    def _authorized_target(self, requested_path: str) -> Path | None:
        candidate = Path(requested_path)
        unresolved = candidate if candidate.is_absolute() else self._workspace / candidate
        try:
            target = unresolved.resolve(strict=False)
            target.relative_to(self._workspace)
        except (OSError, RuntimeError, ValueError):
            return None
        if self._is_sensitive_target(target):
            return None
        return target

    def _is_sensitive_target(self, target: Path) -> bool:
        try:
            relative = target.relative_to(self._workspace)
        except ValueError:
            return True
        for part in relative.parts:
            lowered = part.casefold()
            stem = lowered.split(".", maxsplit=1)[0]
            if (
                lowered in SKIPPED_SEARCH_DIRECTORIES
                or lowered in SENSITIVE_FILE_NAMES
                or lowered.startswith(".env.")
                or Path(lowered).suffix in SENSITIVE_SUFFIXES
                or stem in WINDOWS_RESERVED_NAMES
            ):
                return True
        return False

    def _target_error(
        self,
        call: ToolCall,
        requested_path: str,
        target: Path | None,
    ) -> ToolResult | None:
        if target is None:
            return self._error(
                call,
                "permission_denied",
                "目标不在授权工作区内或属于敏感位置",
                requested_path,
            )
        if not target.exists():
            return self._error(call, "not_found", "目标不存在", requested_path)
        return None

    def _relative_path(self, target: Path) -> str:
        try:
            relative = target.resolve(strict=False).relative_to(self._workspace)
        except (OSError, RuntimeError, ValueError):
            return "<outside-workspace>"
        value = relative.as_posix()
        return value if value else "."

    @staticmethod
    def _validate_arguments(tool_name: str, arguments: Any) -> str | None:
        if not isinstance(arguments, dict):
            return "工具参数必须是对象"
        allowed = {
            "list_workspace": {"path", "cursor", "limit"},
            "search_text": {"query", "path", "cursor", "limit"},
            "read_file": {"path", "cursor", "limit"},
            "apply_patch": {"path", "expected_sha256", "old_text", "new_text"},
            "apply_file_operations": {"reason", "operations"},
        }[tool_name]
        if set(arguments) - allowed:
            return "工具参数包含未知字段"
        if tool_name == "apply_file_operations":
            if set(arguments) != {"reason", "operations"}:
                return "apply_file_operations 必须提供 reason 和 operations"
            reason = arguments["reason"]
            operations = arguments["operations"]
            if not isinstance(reason, str) or not reason.strip():
                return "reason 必须是非空字符串"
            if len(reason.encode("utf-8")) > MAX_OPERATION_REASON_BYTES:
                return "reason 超过大小上限"
            if (
                not isinstance(operations, list)
                or not 1 <= len(operations) <= MAX_FILE_OPERATIONS
            ):
                return f"operations 必须包含 1 到 {MAX_FILE_OPERATIONS} 项"
            for operation in operations:
                error = ToolRegistry._validate_file_operation(operation)
                if error is not None:
                    return error
            return None
        if tool_name == "apply_patch":
            required = {"path", "expected_sha256", "old_text", "new_text"}
            if not required.issubset(arguments):
                return "apply_patch 缺少必要字段"
            path = arguments["path"]
            expected_sha256 = arguments["expected_sha256"]
            old_text = arguments["old_text"]
            new_text = arguments["new_text"]
            if not isinstance(path, str) or not path:
                return "path 必须是非空字符串"
            if (
                not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(
                    character not in "0123456789abcdefABCDEF"
                    for character in expected_sha256
                )
            ):
                return "expected_sha256 必须是 64 位十六进制字符串"
            if not isinstance(old_text, str) or not old_text:
                return "old_text 必须是非空字符串"
            if not isinstance(new_text, str) or old_text == new_text:
                return "new_text 必须是与 old_text 不同的字符串"
            if (
                len(old_text.encode("utf-8")) > MAX_PATCH_FRAGMENT_BYTES
                or len(new_text.encode("utf-8")) > MAX_PATCH_FRAGMENT_BYTES
            ):
                return "补丁片段超过大小上限"
            return None
        path = arguments.get("path", ".")
        cursor = arguments.get("cursor", 0)
        default_limit = 200 if tool_name == "read_file" else 100
        limit = arguments.get("limit", default_limit)
        max_limit = 500 if tool_name == "read_file" else 100
        if not isinstance(path, str) or not path:
            return "path 必须是非空字符串"
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            return "cursor 必须是非负整数"
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= max_limit:
            return f"limit 必须是 1 到 {max_limit} 的整数"
        if tool_name == "search_text":
            query = arguments.get("query")
            if not isinstance(query, str) or not query:
                return "query 必须是非空字符串"
        if tool_name == "read_file" and "path" not in arguments:
            return "read_file 必须提供 path"
        return None

    @staticmethod
    def _validate_file_operation(operation: Any) -> str | None:
        if not isinstance(operation, dict):
            return "每个文件操作必须是对象"
        kind = operation.get("kind")
        path = operation.get("path")
        if kind not in {"write_text", "write_binary", "delete", "move"}:
            return "文件操作 kind 无效"
        if not isinstance(path, str) or not path:
            return "文件操作 path 必须是非空字符串"
        required_by_kind = {
            "write_text": {"kind", "path", "content"},
            "write_binary": {"kind", "path", "content_base64"},
            "delete": {"kind", "path"},
            "move": {"kind", "path", "destination"},
        }
        expected = required_by_kind[kind]
        if set(operation) != expected:
            return f"{kind} 参数字段不完整或包含未知字段"
        if kind == "write_text":
            content = operation["content"]
            if not isinstance(content, str):
                return "content 必须是字符串"
            if len(content.encode("utf-8")) > MAX_HIGH_RISK_CONTENT_BYTES:
                return "文本内容超过大小上限"
        elif kind == "write_binary":
            encoded = operation["content_base64"]
            if not isinstance(encoded, str):
                return "content_base64 必须是字符串"
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                return "content_base64 不是有效 Base64"
            if len(content) > MAX_HIGH_RISK_CONTENT_BYTES:
                return "二进制内容超过大小上限"
        elif kind == "move":
            destination = operation["destination"]
            if not isinstance(destination, str) or not destination:
                return "destination 必须是非空字符串"
        return None

    def _success(
        self,
        call: ToolCall,
        target: str,
        payload: Mapping[str, object],
        evidence_paths: tuple[str, ...] = (),
    ) -> ToolResult:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(content.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
            return self._error(
                call,
                "tool_result_too_large",
                "单页工具结果超过大小上限，请缩小 limit 或读取范围",
                target,
            )
        return ToolResult(
            call_id=call.id,
            tool_name=call.name,
            status="success",
            content=content,
            error_code=None,
            target=target,
            evidence_paths=evidence_paths,
        )

    def _error(
        self,
        call: ToolCall,
        error_code: str,
        message: str,
        target: str | None = None,
    ) -> ToolResult:
        return ToolResult(
            call_id=call.id,
            tool_name=call.name,
            status="error",
            content=json.dumps(
                {"error": message, "error_code": error_code},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            error_code=error_code,
            target=target or self.describe_target(call),
        )
