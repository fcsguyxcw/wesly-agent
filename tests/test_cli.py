from collections.abc import Sequence
from io import BytesIO, StringIO, TextIOWrapper

from wesly.cli import run_cli
from wesly.model import ModelProviderError, ModelRequest, ModelTurn, Usage


class ScriptedModelClient:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)

    def complete(self, request: ModelRequest) -> ModelTurn:
        return next(self._turns)


def test_cli_shows_activity_answer_and_usage() -> None:
    stdout = StringIO()
    stderr = StringIO()
    client = ScriptedModelClient(
        [
            ModelTurn(
                content="这是 Wesly。",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=10, output_tokens=4),
            )
        ]
    )

    exit_code = run_cli(
        ["这个项目是什么？"],
        model_client=client,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert stdout.getvalue() == (
        "[model] 正在调用模型\n"
        "[ok] 模型响应完成\n"
        "\n"
        "这是 Wesly。\n"
        "\n"
        "模型轮次: 1 | 工具调用: 0 | tokens: 10 输入 / 4 输出\n"
    )


class FailingModelClient:
    def complete(self, request: ModelRequest) -> ModelTurn:
        raise ModelProviderError("模型服务暂时不可用")


def test_cli_reports_provider_failure_with_a_nonzero_exit() -> None:
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["检查项目"],
        model_client=FailingModelClient(),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == "[model] 正在调用模型\n"
    assert stderr.getvalue() == "[error] provider_error: 模型服务暂时不可用\n"


def test_cli_activity_is_safe_on_a_windows_gbk_stream() -> None:
    output_bytes = BytesIO()
    stdout = TextIOWrapper(output_bytes, encoding="gbk", errors="strict")
    client = ScriptedModelClient(
        [
            ModelTurn(
                content="完成",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=3, output_tokens=1),
            )
        ]
    )

    exit_code = run_cli(
        ["检查项目"],
        model_client=client,
        stdout=stdout,
        stderr=StringIO(),
    )
    stdout.flush()

    assert exit_code == 0
    assert output_bytes.getvalue().decode("gbk").splitlines()[:2] == [
        "[model] 正在调用模型",
        "[ok] 模型响应完成",
    ]
