from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

import pytest

from wesly.cli import run_cli
from wesly.events import ModelCompleted, ModelStarted, RunCompleted
from wesly.model import ModelProviderError, ModelRequest, ModelTurn, ToolCall, Usage
from wesly.sessions import SessionStorageError, SessionStore


class ScriptedModelClient:
    def __init__(self, turns: Sequence[ModelTurn | Exception]) -> None:
        self._turns = iter(turns)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        turn = next(self._turns)
        if isinstance(turn, Exception):
            raise turn
        return turn


def test_session_event_and_projection_update_roll_back_together(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "wesly.db")
    session = store.create_session(tmp_path, "检查项目", ("fixed",))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_projection BEFORE UPDATE ON sessions
            BEGIN SELECT RAISE(ABORT, 'projection failed'); END
            """
        )
        event_count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0]

    with pytest.raises(SessionStorageError, match="projection failed"):
        store.append_event(
            session.session_id,
            RunCompleted("完成", 1, 0, Usage(3, 1)),
        )

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0] == event_count
        assert connection.execute(
            "SELECT status FROM sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0] == "running"
    store.close()


def test_model_retries_append_attempts_and_usage(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "wesly.db")
    session = store.create_session(tmp_path, "检查项目", ("fixed",))

    store.append_event(session.session_id, ModelStarted(1))
    store.append_event(session.session_id, ModelCompleted(1, "stop", Usage(4, 2)))
    store.append_event(session.session_id, ModelStarted(1))
    store.append_event(session.session_id, ModelCompleted(1, "stop", Usage(7, 3)))

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            """
            SELECT attempt, status, input_tokens, output_tokens
            FROM model_attempts WHERE session_id = ? ORDER BY attempt
            """,
            (session.session_id,),
        ).fetchall() == [
            (1, "completed", 4, 2),
            (2, "completed", 7, 3),
        ]
    store.close()


def test_cli_resume_uses_original_history_and_pinned_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    instruction_file = tmp_path / "WESLY.md"
    instruction_file.write_text("first instructions", encoding="utf-8")
    store = SessionStore(tmp_path / "wesly.db")
    first_client = ScriptedModelClient(
        [
            ModelTurn(
                None,
                (ToolCall("list", "list_workspace", '{"path":"."}'),),
                "tool_calls",
                Usage(4, 2),
            ),
            ModelProviderError("暂时失败"),
        ]
    )

    assert run_cli(
        ["检查项目"],
        model_client=first_client,
        stdout=StringIO(),
        stderr=StringIO(),
        session_store=store,
    ) == 1
    session = store.list_sessions(tmp_path)[0]
    assert session.status == "failed"
    instruction_file.write_text("changed instructions", encoding="utf-8")

    resumed_client = ScriptedModelClient(
        [ModelTurn("恢复完成", (), "stop", Usage(5, 2))]
    )
    assert run_cli(
        [],
        model_client=resumed_client,
        stdout=StringIO(),
        stderr=StringIO(),
        session_store=store,
        resume=True,
        resume_session_id=session.session_id,
    ) == 0

    request = resumed_client.requests[0]
    assert request.messages[0].content == "检查项目"
    assert request.messages[1].role == "assistant"
    assert request.messages[1].tool_calls[0].name == "list_workspace"
    assert request.messages[2].role == "tool"
    assert "first instructions" in "\n".join(request.instructions)
    assert "changed instructions" not in "\n".join(request.instructions)
    sessions = store.list_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0].status == "completed"
    store.close()


def test_default_runs_create_distinct_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = SessionStore(tmp_path / "wesly.db")
    for task in ("第一个任务", "第二个任务"):
        assert run_cli(
            [task],
            model_client=ScriptedModelClient(
                [ModelTurn("完成", (), "stop", Usage(2, 1))]
            ),
            stdout=StringIO(),
            stderr=StringIO(),
            session_store=store,
        ) == 0

    sessions = store.list_sessions(tmp_path)
    assert len(sessions) == 2
    assert {session.goal for session in sessions} == {"第一个任务", "第二个任务"}
    store.close()
