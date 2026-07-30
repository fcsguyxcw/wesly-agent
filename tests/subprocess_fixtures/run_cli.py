import os
import sys
from collections.abc import Sequence
from pathlib import Path

from wesly.cli import run_cli
from wesly.model import (
    ModelClient,
    ModelProviderError,
    ModelRequest,
    ModelTurn,
    ToolCall,
    Usage,
)
from wesly.sessions import SessionStore


class SuccessfulModelClient:
    def complete(self, request: ModelRequest) -> ModelTurn:
        return ModelTurn(
            content="子进程回答",
            tool_calls=(),
            finish_reason="stop",
            usage=Usage(input_tokens=5, output_tokens=2),
        )


class FailingModelClient:
    def complete(self, request: ModelRequest) -> ModelTurn:
        raise ModelProviderError("模型服务暂时不可用")


class InterruptingModelClient:
    def complete(self, request: ModelRequest) -> ModelTurn:
        raise KeyboardInterrupt


class ScriptedModelClient:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)

    def complete(self, request: ModelRequest) -> ModelTurn:
        return next(self._turns)


def tool_turn(call: ToolCall) -> ModelTurn:
    return ModelTurn(
        content=None,
        tool_calls=(call,),
        finish_reason="tool_calls",
        usage=Usage(input_tokens=3, output_tokens=2),
    )


def final_turn(content: str) -> ModelTurn:
    return ModelTurn(
        content=content,
        tool_calls=(),
        finish_reason="stop",
        usage=Usage(input_tokens=5, output_tokens=3),
    )


mode = sys.argv[1]
client: ModelClient
if mode == "failure":
    client = FailingModelClient()
elif mode == "search-read":
    client = ScriptedModelClient(
        [
            tool_turn(ToolCall("search", "search_text", '{"query":"Wesly","path":"README.md"}')),
            tool_turn(ToolCall("read", "read_file", '{"path":"README.md","limit":5}')),
            final_turn("项目说明见 [[README.md]]。"),
        ]
    )
elif mode == "no-evidence":
    client = ScriptedModelClient([final_turn("项目说明见 [[README.md]]。")])
elif mode == "session-interrupt":
    client = InterruptingModelClient()
elif mode == "session-resume":
    client = SuccessfulModelClient()
else:
    client = SuccessfulModelClient()

database_path = os.environ.get("WESLY_TEST_DB")
store = SessionStore(Path(database_path)) if database_path else None
try:
    raise SystemExit(
        run_cli(
            [] if mode == "session-resume" else ["检查项目"],
            model_client=client,
            stdout=sys.stdout,
            stderr=sys.stderr,
            session_store=store,
            resume=mode == "session-resume",
        )
    )
finally:
    if store is not None:
        store.close()
