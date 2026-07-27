from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from wesly.agent import Agent
from wesly.context import InstructionLoadError, ReadOnlyContextBuilder
from wesly.deepseek import create_deepseek_adapter
from wesly.events import (
    ModelCompleted,
    ModelStarted,
    RunCompleted,
    RunFailed,
    ToolCompleted,
    ToolStarted,
)
from wesly.model import ModelClient
from wesly.tools import ToolRegistry


def run_cli(
    args: Sequence[str],
    *,
    model_client: ModelClient,
    stdout: TextIO,
    stderr: TextIO,
    verbose: bool = False,
) -> int:
    task = args[0]
    workspace = Path.cwd()
    try:
        context_builder = ReadOnlyContextBuilder(workspace)
    except KeyboardInterrupt:
        print("[error] interrupted: 任务已由用户中断", file=stderr)
        return 130
    except InstructionLoadError as error:
        print(f"[error] {error.error_code}: {error}", file=stderr)
        return 1
    agent = Agent(
        model_client,
        context_builder=context_builder,
        tool_registry=ToolRegistry(workspace),
    )
    try:
        for event in agent.run(task):
            if isinstance(event, ModelStarted):
                print("[model] 正在调用模型", file=stdout)
                if verbose:
                    print(f"[detail] model_turn={event.turn}", file=stdout)
            elif isinstance(event, ModelCompleted):
                print("[ok] 模型响应完成", file=stdout)
                if verbose:
                    print(
                        f"[detail] finish_reason={_safe_activity_text(event.finish_reason)} "
                        f"input_tokens={event.usage.input_tokens} "
                        f"output_tokens={event.usage.output_tokens}",
                        file=stdout,
                    )
            elif isinstance(event, ToolStarted):
                print(
                    f"[tool] {_safe_activity_text(event.tool_name)} "
                    f"目标: {_safe_activity_text(event.target)}",
                    file=stdout,
                )
                if verbose:
                    print(
                        f"[detail] tool_call_id={_safe_activity_text(event.call_id)}",
                        file=stdout,
                    )
            elif isinstance(event, ToolCompleted):
                label = "ok" if event.status == "success" else "error"
                print(
                    f"[{label}] {_safe_activity_text(event.tool_name)} {event.status}",
                    file=stdout,
                )
                if verbose:
                    error_code = event.error_code or "none"
                    print(
                        f"[detail] tool_call_id={_safe_activity_text(event.call_id)} "
                        f"error_code={_safe_activity_text(error_code)}",
                        file=stdout,
                    )
            elif isinstance(event, RunCompleted):
                print("[done] 运行完成", file=stdout)
                print(file=stdout)
                print(event.answer, file=stdout)
                if event.evidence_paths:
                    print(file=stdout)
                    print(
                        "文件证据: " + ", ".join(event.evidence_paths),
                        file=stdout,
                    )
                print(file=stdout)
                print(
                    f"模型轮次: {event.model_turns} | "
                    f"工具调用: {event.tool_calls} | "
                    f"tokens: {event.usage.input_tokens} 输入 / "
                    f"{event.usage.output_tokens} 输出",
                    file=stdout,
                )
                return 0
            elif isinstance(event, RunFailed):
                message = _redact_secrets(event.message)
                print(f"[error] {event.stop_reason}: {message}", file=stderr)
                return 130 if event.stop_reason == "interrupted" else 1
    except KeyboardInterrupt:
        print("[error] interrupted: 任务已由用户中断", file=stderr)
        return 130
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    _configure_standard_streams()
    parser = argparse.ArgumentParser(prog="wesly")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("task")
    arguments = parser.parse_args(argv)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误: 未设置 DEEPSEEK_API_KEY", file=sys.stderr)
        return 2

    model = os.environ.get("WESLY_MODEL", "deepseek-v4-pro")
    model_client = create_deepseek_adapter(api_key=api_key, model=model)
    return run_cli(
        [arguments.task],
        model_client=model_client,
        stdout=sys.stdout,
        stderr=sys.stderr,
        verbose=arguments.verbose,
    )


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _safe_activity_text(value: str) -> str:
    return value.encode("unicode_escape").decode("ascii")


def _redact_secrets(value: str) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if api_key:
        return value.replace(api_key, "[REDACTED]")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
