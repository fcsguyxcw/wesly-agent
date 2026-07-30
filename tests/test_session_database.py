from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from wesly.events import ModelCompleted, ModelStarted, ToolCompleted
from wesly.model import Message, Usage
from wesly.sessions import SessionStorageError, SessionStore


def _create_v1_database(database_path: Path, workspace: Path) -> str:
    session_id = "00000000-0000-0000-0000-000000000001"
    normalized_workspace = os.path.normcase(str(workspace.resolve(strict=True)))
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                workspace TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('running', 'completed', 'failed', 'interrupted')
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                instructions_json TEXT NOT NULL
            );
            CREATE INDEX sessions_workspace_updated
            ON sessions(workspace, updated_at DESC);
            CREATE TABLE events (
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(session_id, sequence)
            );
            CREATE TABLE model_attempts (
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                attempt INTEGER NOT NULL,
                event_sequence INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
                input_tokens INTEGER,
                output_tokens INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY(session_id, attempt)
            );
            PRAGMA user_version = 1;
            """
        )
        connection.execute(
            """
            INSERT INTO sessions VALUES (?, ?, '检查项目', 'running', ?, ?, ?)
            """,
            (
                session_id,
                normalized_workspace,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                json.dumps(["fixed"]),
            ),
        )
        connection.execute(
            """
            INSERT INTO events VALUES (?, 1, 'future_event', 1, ?, ?)
            """,
            (
                session_id,
                json.dumps({"known": 1, "future": {"value": 2}}, sort_keys=True),
                "2026-01-01T00:00:00+00:00",
            ),
        )
    return session_id


def test_v1_database_migrates_transactionally_after_recoverable_backup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "wesly.db"
    session_id = _create_v1_database(database_path, tmp_path)

    store = SessionStore(database_path)
    assert store.get_session(session_id).goal == "检查项目"
    store.close()

    backups = tuple(tmp_path.glob("wesly.db.v1.*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT payload_json FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == json.dumps(
            {"known": 1, "future": {"value": 2}}, sort_keys=True
        )
        connection.execute(
            "UPDATE sessions SET status = 'outcome_unknown' WHERE session_id = ?",
            (session_id,),
        )
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute(
            "SELECT goal FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()[0] == "检查项目"


def test_failed_migration_rolls_back_original_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "wesly.db"
    session_id = _create_v1_database(database_path, tmp_path)

    def fail_after_write(store: SessionStore) -> None:
        store._connection.execute("CREATE TABLE migration_partial(value TEXT)")
        raise sqlite3.OperationalError("forced migration failure")

    monkeypatch.setattr(SessionStore, "_migrate_v1_to_v2", fail_after_write)
    with pytest.raises(SessionStorageError, match="原库已回滚"):
        SessionStore(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute(
            "SELECT goal FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()[0] == "检查项目"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'migration_partial'"
        ).fetchone()[0] == 0
    assert len(tuple(tmp_path.glob("wesly.db.v1.*.bak"))) == 1


def test_newer_database_is_rejected_without_writes(tmp_path: Path) -> None:
    database_path = tmp_path / "wesly.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 99")
    original_bytes = database_path.read_bytes()

    with pytest.raises(SessionStorageError, match="高于当前支持版本 2"):
        SessionStore(database_path)

    assert database_path.read_bytes() == original_bytes


def test_delete_session_cascades_only_after_explicit_store_call(tmp_path: Path) -> None:
    database_path = tmp_path / "wesly.db"
    store = SessionStore(database_path)
    session = store.create_session(tmp_path, "检查项目", ("fixed",))
    store.append_event(session.session_id, ModelStarted(1))
    store.append_event(session.session_id, ModelCompleted(1, "stop", Usage(2, 1)))
    store.close()

    reopened = SessionStore(database_path)
    assert reopened.get_session(session.session_id).goal == "检查项目"
    reopened.delete_session(tmp_path, session.session_id)
    reopened.close()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM model_attempts").fetchone()[0] == 0


def test_delete_failure_rolls_back_session_and_history(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "wesly.db")
    session = store.create_session(tmp_path, "检查项目", ("fixed",))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_delete BEFORE DELETE ON sessions
            BEGIN SELECT RAISE(ABORT, 'delete rejected'); END
            """
        )

    with pytest.raises(SessionStorageError, match="delete rejected"):
        store.delete_session(tmp_path, session.session_id)

    assert store.get_session(session.session_id).goal == "检查项目"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?", (session.session_id,)
        ).fetchone()[0] == 1
    store.close()


def test_sensitive_values_are_redacted_before_any_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "token-value-that-must-not-persist"
    patterned_secret = "sk-1234567890abcdef"
    monkeypatch.setenv("WESLY_TEST_TOKEN", secret)
    store = SessionStore(tmp_path / "wesly.db")
    session = store.create_session(
        tmp_path,
        f"检查 {secret}",
        (f"instruction {patterned_secret}",),
    )
    store.append_event(
        session.session_id,
        ToolCompleted(
            "call",
            "read_file",
            "success",
            "README.md",
            None,
            message=Message("tool", f"{secret} {patterned_secret}", tool_call_id="call"),
        ),
    )
    store.close()

    with sqlite3.connect(tmp_path / "wesly.db") as connection:
        persisted = "\n".join(
            [
                *connection.execute(
                    "SELECT goal || instructions_json FROM sessions"
                ).fetchone(),
                *(row[0] for row in connection.execute("SELECT payload_json FROM events")),
            ]
        )
        assert connection.execute(
            "SELECT DISTINCT payload_version FROM events"
        ).fetchall() == [(1,)]
    assert secret not in persisted
    assert patterned_secret not in persisted
    assert "[REDACTED]" in persisted
