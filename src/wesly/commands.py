from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from wesly.model import ToolCall
from wesly.permissions import NormalizedCommand, PreparedOperation
from wesly.process_tree import ProcessTreeError, spawn_process_tree


MAX_COMMAND_OUTPUT_BYTES = 12 * 1024
MAX_COMMAND_REASON_BYTES = 2 * 1024
MAX_COMMAND_ARGUMENT_BYTES = 32 * 1024
MAX_COMMAND_ENVIRONMENT_ENTRIES = 64
MAX_COMMAND_TIMEOUT_SECONDS = 600
SENSITIVE_ENV_FRAGMENTS = ("KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")


@dataclass(frozen=True, slots=True)
class CommandExecution:
    status: Literal["success", "error"]
    error_code: str | None
    payload: Mapping[str, object]


class CommandRunner:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve(strict=True)

    @staticmethod
    def validate_arguments(arguments: Any) -> str | None:
        if not isinstance(arguments, dict):
            return "工具参数必须是对象"
        common = {"mode", "cwd", "env", "timeout_seconds", "reason", "purpose"}
        mode = arguments.get("mode")
        if mode == "argv":
            required = common | {"executable", "args"}
        elif mode == "powershell":
            required = common | {"powershell_script"}
        else:
            return "mode 必须是 argv 或 powershell"
        if set(arguments) != required:
            return "run_command 参数缺失或包含未知字段"
        if not isinstance(arguments["cwd"], str) or not arguments["cwd"]:
            return "cwd 必须是非空字符串"
        reason = arguments["reason"]
        if not isinstance(reason, str) or not reason.strip():
            return "reason 必须是非空字符串"
        if len(reason.encode("utf-8")) > MAX_COMMAND_REASON_BYTES:
            return "reason 超过大小上限"
        if not isinstance(arguments["purpose"], str) or arguments["purpose"] not in {
            "inspect",
            "verify",
            "build",
            "modify",
            "other",
        }:
            return "purpose 必须是 inspect、verify、build、modify 或 other"
        timeout = arguments["timeout_seconds"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_COMMAND_TIMEOUT_SECONDS:
            return f"timeout_seconds 必须是 1 到 {MAX_COMMAND_TIMEOUT_SECONDS} 的整数"
        env = arguments["env"]
        if not isinstance(env, dict) or len(env) > MAX_COMMAND_ENVIRONMENT_ENTRIES:
            return f"env 必须是至多 {MAX_COMMAND_ENVIRONMENT_ENTRIES} 项的对象"
        if any(not isinstance(key, str) or not key or "=" in key for key in env):
            return "env 键必须是非空且不含等号的字符串"
        if any(not isinstance(value, str) for value in env.values()):
            return "env 值必须是字符串"
        if mode == "argv":
            executable = arguments["executable"]
            args = arguments["args"]
            if not isinstance(executable, str) or not executable:
                return "executable 必须是非空字符串"
            if not isinstance(args, list) or any(not isinstance(value, str) for value in args):
                return "args 必须是字符串数组"
        else:
            script = arguments["powershell_script"]
            if not isinstance(script, str) or not script.strip():
                return "powershell_script 必须是非空字符串"
        if len(json.dumps(arguments, ensure_ascii=False).encode("utf-8")) > MAX_COMMAND_ARGUMENT_BYTES:
            return "run_command 参数超过大小上限"
        return None

    def prepare(self, call: ToolCall, arguments: dict[str, Any]) -> PreparedOperation | str:
        cwd = self._resolve_cwd(arguments["cwd"])
        if cwd is None:
            return "cwd 不存在、不是目录或不在授权工作区内"

        mode: Literal["argv", "powershell"] = arguments["mode"]
        if mode == "argv":
            executable = self._resolve_executable(arguments["executable"], cwd)
            if executable is None:
                return "executable 不存在或不是可执行文件"
            argv = (str(executable), *arguments["args"])
        else:
            executable = self._resolve_executable("powershell.exe", cwd)
            if executable is None:
                return "找不到 powershell.exe"
            argv = (
                str(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                arguments["powershell_script"],
            )

        env = tuple(sorted(arguments["env"].items()))
        sensitive_values = tuple(
            value
            for key, value in env
            if value and any(fragment in key.upper() for fragment in SENSITIVE_ENV_FRAGMENTS)
        )
        command = NormalizedCommand(
            mode=mode,
            executable=executable,
            executable_sha256=self._sha256(executable),
            argv=argv,
            cwd=cwd,
            env=env,
            timeout_seconds=arguments["timeout_seconds"],
            purpose=arguments["purpose"],
            redacted_values=sensitive_values,
        )
        bound = {
            "mode": mode,
            "executable": str(executable),
            "executable_sha256": command.executable_sha256,
            "argv": argv,
            "cwd": str(cwd),
            "env": env,
            "timeout_seconds": command.timeout_seconds,
            "purpose": command.purpose,
            "reason": arguments["reason"],
        }
        fingerprint = hashlib.sha256(
            json.dumps(bound, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        displayed = dict(arguments)
        displayed["env"] = {
            key: "[REDACTED]" if value in sensitive_values else value for key, value in env
        }
        if mode == "powershell":
            script = displayed.pop("powershell_script")
            parameters = (
                json.dumps(displayed, ensure_ascii=False, sort_keys=True)
                + "\npowershell_script:\n"
                + str(script)
            )
        else:
            parameters = json.dumps(displayed, ensure_ascii=False, sort_keys=True)
        return PreparedOperation(
            call_id=call.id,
            arguments_json=call.arguments_json,
            fingerprint=fingerprint,
            operation=call.name,
            parameters=parameters,
            resolved_targets=(str(executable), str(cwd)),
            reason=arguments["reason"],
            impact_scope="启动 1 个进程；可读写当前用户有权访问的资源",
            workspace=str(self._workspace),
            sensitivity="sensitive",
            effects=(),
            command=command,
        )

    def execute(self, command: NormalizedCommand) -> CommandExecution:
        process_env = os.environ.copy()
        process_env.update(command.env)
        redacted_values = tuple(
            value
            for key, value in process_env.items()
            if value and any(fragment in key.upper() for fragment in SENSITIVE_ENV_FRAGMENTS)
        )
        try:
            tree = spawn_process_tree(
                command.argv,
                cwd=command.cwd,
                env=process_env,
            )
        except ProcessTreeError:
            return CommandExecution(
                "error",
                "command_start_failed",
                {"error": "命令进程启动失败", "error_code": "command_start_failed"},
            )

        process = tree.process
        try:
            try:
                stdout, stderr = process.communicate(timeout=command.timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    tree.terminate()
                except ProcessTreeError:
                    tree.close()
                    stdout, stderr = process.communicate()
                    failed_payload = dict(
                        self._payload(
                            stdout,
                            stderr,
                            process.returncode,
                            redacted_values,
                            timed_out=True,
                        )
                    )
                    failed_payload["error_code"] = "command_termination_failed"
                    return CommandExecution(
                        "error",
                        "command_termination_failed",
                        failed_payload,
                    )
                stdout, stderr = process.communicate()
                timeout_payload = dict(
                    self._payload(
                        stdout,
                        stderr,
                        process.returncode,
                        redacted_values,
                        timed_out=True,
                    )
                )
                timeout_payload["error_code"] = "command_timeout"
                return CommandExecution(
                    "error",
                    "command_timeout",
                    timeout_payload,
                )
            except KeyboardInterrupt:
                try:
                    try:
                        tree.terminate()
                    except ProcessTreeError:
                        pass
                finally:
                    tree.close()
                    process.communicate()
                raise
        finally:
            tree.close()

        payload = self._payload(
            stdout,
            stderr,
            process.returncode,
            redacted_values,
            timed_out=False,
        )
        if process.returncode != 0:
            failed_payload = dict(payload)
            failed_payload["error_code"] = "command_nonzero"
            return CommandExecution("error", "command_nonzero", failed_payload)
        return CommandExecution("success", None, payload)

    def _payload(
        self,
        stdout: bytes,
        stderr: bytes,
        exit_code: int | None,
        redacted_values: tuple[str, ...],
        *,
        timed_out: bool,
    ) -> Mapping[str, object]:
        return {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": self._bounded_stream(stdout, redacted_values),
            "stderr": self._bounded_stream(stderr, redacted_values),
        }

    @staticmethod
    def _bounded_stream(data: bytes, redacted_values: tuple[str, ...]) -> Mapping[str, object]:
        text = data.decode("utf-8", errors="replace")
        for value in redacted_values:
            text = text.replace(value, "[REDACTED]")
        encoded = text.encode("utf-8")
        returned = encoded[:MAX_COMMAND_OUTPUT_BYTES]
        while returned:
            try:
                bounded_text = returned.decode("utf-8")
                break
            except UnicodeDecodeError:
                returned = returned[:-1]
        else:
            bounded_text = ""
        return {
            "text": bounded_text,
            "total_bytes": len(encoded),
            "returned_bytes": len(returned),
            "truncated": len(returned) < len(encoded),
        }

    def _resolve_cwd(self, requested: str) -> Path | None:
        candidate = Path(requested)
        unresolved = candidate if candidate.is_absolute() else self._workspace / candidate
        try:
            resolved = unresolved.resolve(strict=True)
            resolved.relative_to(self._workspace)
        except (OSError, RuntimeError, ValueError):
            return None
        return resolved if resolved.is_dir() else None

    @staticmethod
    def _resolve_executable(requested: str, cwd: Path) -> Path | None:
        candidate = Path(requested)
        if candidate.is_absolute() or candidate.parent != Path("."):
            unresolved = candidate if candidate.is_absolute() else cwd / candidate
            try:
                resolved = unresolved.resolve(strict=True)
            except (OSError, RuntimeError):
                return None
        else:
            located = shutil.which(requested)
            if located is None:
                return None
            try:
                resolved = Path(located).resolve(strict=True)
            except (OSError, RuntimeError):
                return None
        return resolved if resolved.is_file() else None

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
