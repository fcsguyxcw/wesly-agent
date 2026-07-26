from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import TextIO

from wesly.agent import Agent
from wesly.deepseek import create_deepseek_adapter
from wesly.events import ModelCompleted, ModelStarted, RunCompleted, RunFailed
from wesly.model import ModelClient


def run_cli(
    args: Sequence[str],
    *,
    model_client: ModelClient,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    task = args[0]
    for event in Agent(model_client).run(task):
        if isinstance(event, ModelStarted):
            print("[model] 正在调用模型", file=stdout)
        elif isinstance(event, ModelCompleted):
            print("[ok] 模型响应完成", file=stdout)
        elif isinstance(event, RunCompleted):
            print("[done] 运行完成", file=stdout)
            print(file=stdout)
            print(event.answer, file=stdout)
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
            print(f"[error] {event.stop_reason}: {event.message}", file=stderr)
            return 1
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    _configure_standard_streams()
    parser = argparse.ArgumentParser(prog="wesly")
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
    )


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
