from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from wesly.model import ToolCall, ToolResult


MAX_TOOL_RESULT_BYTES = 32 * 1024
MAX_EDIT_FILE_BYTES = 1024 * 1024
MAX_PATCH_FRAGMENT_BYTES = 32 * 1024
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

    def execute(
        self,
        call: ToolCall,
        *,
        preview: FileEditPreview | None = None,
    ) -> ToolResult:
        if call.name not in {"list_workspace", "search_text", "read_file", "apply_patch"}:
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

        if call.name == "list_workspace":
            return self._list_workspace(call, arguments)
        if call.name == "search_text":
            return self._search_text(call, arguments)
        return self._read_file(call, arguments)

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
        }[tool_name]
        if set(arguments) - allowed:
            return "工具参数包含未知字段"
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
