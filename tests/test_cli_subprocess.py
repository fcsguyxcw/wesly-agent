import os
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
