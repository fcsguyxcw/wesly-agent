import sys

from wesly.cli import run_cli
from wesly.model import ModelProviderError, ModelRequest, ModelTurn, Usage


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


client = FailingModelClient() if sys.argv[1] == "failure" else SuccessfulModelClient()
raise SystemExit(
    run_cli(
        ["检查项目"],
        model_client=client,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
)
