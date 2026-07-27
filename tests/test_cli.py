from collections.abc import Sequence
import hashlib
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path

import pytest

import wesly.cli
from wesly.cli import run_cli
from wesly.model import ModelProviderError, ModelRequest, ModelTurn, ToolCall, Usage


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
        "[done] 运行完成\n"
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


def test_cli_returns_130_for_keyboard_interrupt() -> None:
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["检查项目"],
        model_client=InterruptingModelClient(),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 130
    assert stdout.getvalue() == "[model] 正在调用模型\n"
    assert stderr.getvalue() == "[error] interrupted: 任务已由用户中断\n"


def test_cli_returns_130_when_context_creation_is_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt_context(workspace: Path) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(wesly.cli, "ReadOnlyContextBuilder", interrupt_context)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["检查项目"],
        model_client=ScriptedModelClient([]),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 130
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "[error] interrupted: 任务已由用户中断\n"


class InterruptingModelClient:
    def complete(self, request: ModelRequest) -> ModelTurn:
        raise KeyboardInterrupt


def test_verbose_output_adds_safe_diagnostics_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-test-secret-value"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    stdout = StringIO()
    stderr = StringIO()
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
        stderr=stderr,
        verbose=True,
    )

    assert exit_code == 0
    assert "[detail] model_turn=1\n" in stdout.getvalue()
    assert "[detail] finish_reason=stop input_tokens=3 output_tokens=1\n" in stdout.getvalue()
    assert secret not in stdout.getvalue()
    assert secret not in stderr.getvalue()


def test_cli_reports_instruction_limit_before_calling_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "WESLY.md").write_text("x" * (16 * 1024 + 1), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["检查项目"],
        model_client=ScriptedModelClient([]),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue().startswith("[error] instructions_limit: ")


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


def test_cli_shows_a_safe_tool_target_without_printing_directory_content() -> None:
    stdout = StringIO()
    client = ScriptedModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(
                    ToolCall("call-1", "list_workspace", '{"path":"."}'),
                ),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=4, output_tokens=2),
            ),
            ModelTurn(
                content="目录检查完成。",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=8, output_tokens=3),
            ),
        ]
    )

    exit_code = run_cli(
        ["检查目录"],
        model_client=client,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "[tool] list_workspace 目标: .\n" in output
    assert "[ok] list_workspace success\n" in output
    assert '"entries"' not in output
    assert "模型轮次: 2 | 工具调用: 1 | tokens: 12 输入 / 5 输出" in output


def test_cli_escapes_model_controlled_activity_text() -> None:
    stdout = StringIO()
    client = ScriptedModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(
                    ToolCall("bad\nname", "unknown\ntool", '{"path":"bad\\npath"}'),
                ),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=1, output_tokens=1),
            ),
            ModelTurn(
                content="完成",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=2, output_tokens=1),
            ),
        ]
    )

    exit_code = run_cli(
        ["检查目录"],
        model_client=client,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "[tool] unknown\\ntool 目标: bad\\npath\n" in stdout.getvalue()
    assert "[error] unknown\\ntool error\n" in stdout.getvalue()


def test_cli_shows_file_diff_before_applying_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    monkeypatch.chdir(tmp_path)
    stdout = StringIO()
    client = ScriptedModelClient(
        [
            ModelTurn(
                content=None,
                tool_calls=(ToolCall("read", "read_file", '{"path":"sample.py"}'),),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=4, output_tokens=2),
            ),
            ModelTurn(
                content=None,
                tool_calls=(
                    ToolCall(
                        "patch",
                        "apply_patch",
                        (
                            '{"path":"sample.py","expected_sha256":"'
                            + old_hash
                            + '","old_text":"value = 1","new_text":"value = 2"}'
                        ),
                    ),
                ),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=8, output_tokens=3),
            ),
            ModelTurn(
                content="修改完成。",
                tool_calls=(),
                finish_reason="stop",
                usage=Usage(input_tokens=10, output_tokens=2),
            ),
        ]
    )

    exit_code = run_cli(
        ["把 value 改成 2"],
        model_client=client,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert "[diff] sample.py\n" in output
    assert "--- a/sample.py\n+++ b/sample.py\n" in output
    assert output.index("[diff] sample.py") < output.index("[ok] apply_patch success")


def test_cli_shows_normalized_approval_and_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    stdout = StringIO()
    call = ToolCall(
        "effects",
        "apply_file_operations",
        (
            '{"reason":"create result","operations":['
            '{"kind":"write_text","path":"result.txt","content":"secret body"}]}'
        ),
    )
    client = ScriptedModelClient(
        [
            ModelTurn(None, (call,), "tool_calls", Usage(5, 2)),
            ModelTurn("已尊重拒绝。", (), "stop", Usage(7, 3)),
        ]
    )

    exit_code = run_cli(
        ["创建结果"],
        model_client=client,
        stdin=StringIO("n\n"),
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert not (tmp_path / "result.txt").exists()
    assert "[approval] 高风险文件操作需要本次批准\n" in output
    assert "操作: apply_file_operations\n" in output
    assert "参数: " in output and "content_sha256" in output
    assert "secret body" not in output
    assert "解析目标: " in output and "result.txt" in output
    assert "原因: create result\n" in output
    assert "影响范围: 1 file effect: create_text\n" in output
    assert "敏感性: normal\n" in output
    assert "工作区: " in output
    assert "操作指纹: " in output
    assert "[approval] 拒绝\n" in output
    assert "[error] apply_file_operations error\n" in output


def test_cli_interrupt_during_approval_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptingInput(StringIO):
        def readline(self, size: int = -1) -> str:
            raise KeyboardInterrupt

    monkeypatch.chdir(tmp_path)
    call = ToolCall(
        "effects",
        "apply_file_operations",
        (
            '{"reason":"create result","operations":['
            '{"kind":"write_text","path":"result.txt","content":"done"}]}'
        ),
    )
    client = ScriptedModelClient(
        [ModelTurn(None, (call,), "tool_calls", Usage(5, 2))]
    )
    stderr = StringIO()

    exit_code = run_cli(
        ["创建结果"],
        model_client=client,
        stdin=InterruptingInput(),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 130
    assert not (tmp_path / "result.txt").exists()
    assert stderr.getvalue() == "[error] interrupted: 任务已由用户中断\n"
