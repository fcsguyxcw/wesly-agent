from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wesly.model import ToolCall, ToolResult


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
        if call.name != "list_workspace":
            return self._error(call, "unknown_tool", "未知工具", "<unknown>")

        try:
            arguments = json.loads(call.arguments_json)
        except (json.JSONDecodeError, TypeError):
            return self._error(call, "invalid_json", "工具参数不是有效 JSON")

        validation_error = self._validate_list_arguments(arguments)
        if validation_error is not None:
            return self._error(call, "invalid_arguments", validation_error)

        assert isinstance(arguments, dict)
        requested_path = arguments.get("path", ".")
        assert isinstance(requested_path, str)
        target = self._resolve_workspace_target(requested_path)
        if target is None:
            return self._error(
                call,
                "permission_denied",
                "目标不在授权工作区内",
                requested_path,
            )
        if not target.exists():
            return self._error(
                call,
                "not_found",
                "目标不存在",
                requested_path,
            )
        if not target.is_dir():
            return self._error(
                call,
                "not_a_directory",
                "目标不是可读取目录",
                requested_path,
            )

        cursor = arguments.get("cursor", 0)
        limit = arguments.get("limit", 100)
        assert isinstance(cursor, int) and isinstance(limit, int)
        try:
            children = sorted(target.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            return self._error(call, "read_failed", "目录读取失败", requested_path)

        selected = children[cursor : cursor + limit]
        end = cursor + len(selected)
        has_more = end < len(children)
        payload = {
            "path": requested_path,
            "entries": [
                {
                    "name": child.name,
                    "kind": "directory" if child.is_dir() else "file",
                }
                for child in selected
            ],
            "range": {"start": cursor, "end": end},
            "has_more": has_more,
            "next_cursor": end if has_more else None,
        }
        return ToolResult(
            call_id=call.id,
            tool_name=call.name,
            status="success",
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            error_code=None,
            target=requested_path,
        )

    def _resolve_workspace_target(self, requested_path: str) -> Path | None:
        candidate = Path(requested_path)
        unresolved = candidate if candidate.is_absolute() else self._workspace / candidate
        try:
            target = unresolved.resolve(strict=False)
            target.relative_to(self._workspace)
        except (OSError, RuntimeError, ValueError):
            return None
        return target

    @staticmethod
    def _validate_list_arguments(arguments: Any) -> str | None:
        if not isinstance(arguments, dict):
            return "工具参数必须是对象"
        if set(arguments) - {"path", "cursor", "limit"}:
            return "工具参数包含未知字段"
        path = arguments.get("path", ".")
        cursor = arguments.get("cursor", 0)
        limit = arguments.get("limit", 100)
        if not isinstance(path, str) or not path:
            return "path 必须是非空字符串"
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            return "cursor 必须是非负整数"
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            return "limit 必须是 1 到 100 的整数"
        return None

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
