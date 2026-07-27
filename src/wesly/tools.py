from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from wesly.model import ToolCall, ToolResult


MAX_TOOL_RESULT_BYTES = 32 * 1024
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

READ_ONLY_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    {
        "type": "function",
        "function": {
            "name": "list_workspace",
            "description": "List one directory inside the authorized workspace.",
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
            "description": "Search UTF-8 text inside one workspace file or directory.",
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
            "description": "Read a page of lines from one UTF-8 workspace file.",
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
)


class ToolRegistry:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve(strict=True)

    def describe_target(self, call: ToolCall) -> str:
        try:
            arguments = json.loads(call.arguments_json)
        except (json.JSONDecodeError, TypeError):
            return "<invalid>"
        if not isinstance(arguments, dict):
            return "<invalid>"
        path = arguments.get("path", ".")
        return path if isinstance(path, str) else "<invalid>"

    def execute(self, call: ToolCall) -> ToolResult:
        if call.name not in {"list_workspace", "search_text", "read_file"}:
            return self._error(call, "unknown_tool", "未知工具", "<unknown>")
        try:
            arguments = json.loads(call.arguments_json)
        except (json.JSONDecodeError, TypeError):
            return self._error(call, "invalid_json", "工具参数不是有效 JSON")

        validation_error = self._validate_arguments(call.name, arguments)
        if validation_error is not None:
            return self._error(call, "invalid_arguments", validation_error)
        assert isinstance(arguments, dict)

        if call.name == "list_workspace":
            return self._list_workspace(call, arguments)
        if call.name == "search_text":
            return self._search_text(call, arguments)
        return self._read_file(call, arguments)

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
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return self._error(call, "decode_failed", "文件不是有效 UTF-8 文本", requested_path)
        except OSError:
            return self._error(call, "read_failed", "文件读取失败", requested_path)

        lines = text.splitlines()
        selected = lines[cursor : cursor + limit]
        end = cursor + len(selected)
        truncated = end < len(lines)
        relative_path = self._relative_path(target)
        return self._success(
            call,
            requested_path,
            {
                "path": relative_path,
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
        }[tool_name]
        if set(arguments) - allowed:
            return "工具参数包含未知字段"
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
