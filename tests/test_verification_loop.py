import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

import pytest

from wesly.cli import run_cli
from wesly.model import ModelProviderError, ModelRequest, ModelTurn, ToolCall, Usage


class RecordingModelClient:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        return next(self._turns)


class FailingAfterTurnsClient(RecordingModelClient):
    def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        try:
            return next(self._turns)
        except StopIteration as error:
            raise ModelProviderError("验证失败后模型服务不可用") from error


def turn(call: ToolCall) -> ModelTurn:
    return ModelTurn(None, (call,), "tool_calls", Usage(10, 3))


def command(call_id: str) -> ToolCall:
    return ToolCall(
        call_id,
        "run_command",
        json.dumps(
            {
                "mode": "argv",
                "executable": sys.executable,
                "args": [
                    "-c",
                    "import runpy; assert runpy.run_path('calc.py')['add'](2, 3) == 5",
                ],
                "cwd": ".",
                "env": {},
                "timeout_seconds": 10,
                "reason": "验证 add 函数行为",
            },
            ensure_ascii=False,
        ),
    )


def test_cli_completes_fix_verify_refine_loop_in_real_temporary_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is required for the temporary repository fixture")
    subprocess.run(
        [git, "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    target = tmp_path / "calc.py"
    target.write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    original = target.read_bytes()
    wrong = original.replace(b"left - right", b"left * right")
    corrected = original.replace(b"left - right", b"left + right")
    original_hash = hashlib.sha256(original).hexdigest()
    wrong_hash = hashlib.sha256(wrong).hexdigest()
    client = RecordingModelClient(
        [
            turn(ToolCall("read-original", "read_file", '{"path":"calc.py"}')),
            turn(
                ToolCall(
                    "wrong-fix",
                    "apply_patch",
                    json.dumps(
                        {
                            "path": "calc.py",
                            "expected_sha256": original_hash,
                            "old_text": "left - right",
                            "new_text": "left * right",
                        }
                    ),
                )
            ),
            turn(command("verify-failed")),
            turn(ToolCall("read-failed-fix", "read_file", '{"path":"calc.py"}')),
            turn(
                ToolCall(
                    "correct-fix",
                    "apply_patch",
                    json.dumps(
                        {
                            "path": "calc.py",
                            "expected_sha256": wrong_hash,
                            "old_text": "left * right",
                            "new_text": "left + right",
                        }
                    ),
                )
            ),
            turn(command("verify-passed")),
            ModelTurn(
                "已修复并通过验证 [[calc.py]]",
                (),
                "stop",
                Usage(12, 4),
            ),
        ]
    )
    monkeypatch.chdir(tmp_path)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["修复 add 函数并验证"],
        model_client=client,
        stdin=StringIO("y\ny\n"),
        stdout=stdout,
        stderr=stderr,
    )

    output = stdout.getvalue()
    failed_result = json.loads(client.requests[3].messages[-1].content or "{}")
    passed_result = json.loads(client.requests[6].messages[-1].content or "{}")
    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert target.read_bytes() == corrected
    assert failed_result["error_code"] == "command_nonzero"
    assert failed_result["exit_code"] == 1
    assert passed_result["exit_code"] == 0
    assert output.count("[approval] 命令执行需要本次批准\n") == 2
    assert "变更证据: calc.py\n" in output
    assert "验证结果:\n- 失败: exit=1 (command_nonzero)\n- 通过: exit=0\n" in output
    assert "模型轮次: 7 | 工具调用: 6 | tokens: 72 输入 / 22 输出\n" in output


def test_cli_reports_incomplete_fix_loop_with_last_verified_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "calc.py"
    target.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
    original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    client = FailingAfterTurnsClient(
        [
            turn(ToolCall("read", "read_file", '{"path":"calc.py"}')),
            turn(
                ToolCall(
                    "wrong-fix",
                    "apply_patch",
                    json.dumps(
                        {
                            "path": "calc.py",
                            "expected_sha256": original_hash,
                            "old_text": "left - right",
                            "new_text": "left * right",
                        }
                    ),
                )
            ),
            turn(command("verify-failed")),
        ]
    )
    monkeypatch.chdir(tmp_path)
    stderr = StringIO()

    exit_code = run_cli(
        ["修复 add 函数并验证"],
        model_client=client,
        stdin=StringIO("y\n"),
        stdout=StringIO(),
        stderr=stderr,
    )

    output = stderr.getvalue()
    assert exit_code == 1
    assert "[error] provider_error: 验证失败后模型服务不可用\n" in output
    assert "最后动作: run_command error\n" in output
    assert "变更证据: calc.py\n" in output
    assert "验证结果:\n- 失败: exit=1 (command_nonzero)\n" in output
    assert "运行统计: 模型轮次 4 | 工具调用 3\n" in output
    assert "建议: 检查模型服务后重试\n" in output
