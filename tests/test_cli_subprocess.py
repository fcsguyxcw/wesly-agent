import os
from pathlib import Path
import subprocess
import sys


def test_cli_process_stops_before_network_when_api_key_is_missing() -> None:
    environment = os.environ.copy()
    environment.pop("DEEPSEEK_API_KEY", None)
    environment["PYTHONUTF8"] = "0"

    result = subprocess.run(
        [sys.executable, "-m", "wesly.cli", "检查项目"],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "错误: 未设置 DEEPSEEK_API_KEY\n"


def run_scripted_cli(mode: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    fixture = Path(__file__).parent / "subprocess_fixtures" / "run_cli.py"
    return subprocess.run(
        [sys.executable, str(fixture), mode],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )


def test_cli_process_shows_a_successful_run() -> None:
    result = run_scripted_cli("success")

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == (
        "[model] 正在调用模型\n"
        "[ok] 模型响应完成\n"
        "[done] 运行完成\n"
        "\n"
        "子进程回答\n"
        "\n"
        "模型轮次: 1 | 工具调用: 0 | tokens: 5 输入 / 2 输出\n"
    )


def test_cli_process_shows_a_provider_failure() -> None:
    result = run_scripted_cli("failure")

    assert result.returncode == 1
    assert result.stdout == "[model] 正在调用模型\n"
    assert result.stderr == "[error] provider_error: 模型服务暂时不可用\n"
