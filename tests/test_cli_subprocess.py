import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from wesly.sessions import SessionStore


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


def test_cli_process_lists_workspace_sessions_without_api_key(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "Wesly" / "wesly.db")
    session = store.create_session(Path.cwd(), "检查项目", ("fixed",))
    store.close()
    environment = os.environ.copy()
    environment.pop("DEEPSEEK_API_KEY", None)
    environment["LOCALAPPDATA"] = str(tmp_path)
    environment["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "wesly.cli", "sessions"],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert session.session_id in result.stdout
    assert "running" in result.stdout
    assert "检查项目" in result.stdout


def test_cli_process_deletes_session_only_after_exact_confirmation(tmp_path: Path) -> None:
    database_path = tmp_path / "Wesly" / "wesly.db"
    store = SessionStore(database_path)
    session = store.create_session(Path.cwd(), "检查项目", ("fixed",))
    store.close()
    environment = os.environ.copy()
    environment.pop("DEEPSEEK_API_KEY", None)
    environment["LOCALAPPDATA"] = str(tmp_path)
    environment["PYTHONUTF8"] = "1"
    command = [
        sys.executable,
        "-m",
        "wesly.cli",
        "sessions",
        "delete",
        session.session_id,
    ]

    cancelled = subprocess.run(
        command,
        input="yes\n",
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )
    assert cancelled.returncode == 0
    assert "已取消删除" in cancelled.stdout
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    deleted = subprocess.run(
        command,
        input="delete\n",
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )
    assert deleted.returncode == 0
    assert f"已删除 Session {session.session_id}" in deleted.stdout
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def run_scripted_cli(
    mode: str,
    environment_updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment.update(environment_updates or {})
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
    assert result.stderr == (
        "[error] provider_error: 模型服务暂时不可用\n"
        "运行统计: 模型轮次 1 | 工具调用 0\n"
        "建议: 检查模型服务后重试\n"
    )


def test_cli_process_completes_search_read_and_evidence_flow() -> None:
    result = run_scripted_cli("search-read")

    assert result.returncode == 0
    assert result.stderr == ""
    assert "[tool] search_text 目标: README.md\n" in result.stdout
    assert "[tool] read_file 目标: README.md\n" in result.stdout
    assert "项目说明见 [[README.md]]。\n" in result.stdout
    assert "文件证据: README.md\n" in result.stdout


def test_cli_process_rejects_an_unobserved_file_citation() -> None:
    result = run_scripted_cli("no-evidence")

    assert result.returncode == 1
    assert result.stderr == (
        "[error] evidence_error: 模型引用了本次运行未观察的文件: README.md\n"
        "运行统计: 模型轮次 1 | 工具调用 0\n"
        "建议: 重新调查并只引用本次实际观察的文件\n"
    )


def test_cli_process_resumes_session_after_previous_process_exits(tmp_path: Path) -> None:
    database_path = tmp_path / "wesly.db"
    environment = {"WESLY_TEST_DB": str(database_path)}

    interrupted = run_scripted_cli("session-interrupt", environment)
    resumed = run_scripted_cli("session-resume", environment)

    assert interrupted.returncode == 130
    assert "[session] 新建 " in interrupted.stdout
    assert "[error] interrupted:" in interrupted.stderr
    assert resumed.returncode == 0
    assert "[session] 恢复 " in resumed.stdout
    assert "子进程回答" in resumed.stdout
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM sessions").fetchone()[0] == "completed"
        assert connection.execute(
            "SELECT attempt, status FROM model_attempts ORDER BY attempt"
        ).fetchall() == [(1, "failed"), (2, "completed")]
