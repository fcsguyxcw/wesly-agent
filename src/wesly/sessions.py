from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from wesly.events import (
    AgentEvent,
    ModelCompleted,
    ModelStarted,
    RunCompleted,
    RunFailed,
    ToolCompleted,
)
from wesly.model import Message, ToolCall


SCHEMA_VERSION = 1
SessionStatus = Literal[
    "running",
    "completed",
    "failed",
    "interrupted",
]
READ_ONLY_TOOLS = frozenset({"list_workspace", "search_text", "read_file"})
APPROVAL_TOOLS = frozenset({"apply_file_operations", "run_command"})


class SessionStorageError(Exception):
    """A local session database operation failed without a partial commit."""


class SessionOutcomeUnknownError(SessionStorageError):
    """A side-effecting call may have completed before its result was persisted."""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    workspace: str
    goal: str
    status: SessionStatus
    created_at: str
    updated_at: str
    instructions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _IncompleteToolCall:
    call: ToolCall
    target: str
    started: bool
    approved: bool


class SessionStore:
    def __init__(
        self,
        database_path: Path,
        *,
        redact: Callable[[str], str] | None = None,
    ) -> None:
        self.database_path = database_path
        self._redact = redact or (lambda value: value)
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.database_path)
        except (OSError, sqlite3.Error) as error:
            raise SessionStorageError(f"无法打开 Session 数据库: {error}") from error
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        try:
            self._initialize_schema()
        except Exception:
            self._connection.close()
            raise

    @classmethod
    def default(cls, *, redact: Callable[[str], str] | None = None) -> SessionStore:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            base = Path(local_app_data)
        else:
            base = Path.home() / "AppData" / "Local"
        return cls(base / "Wesly" / "wesly.db", redact=redact)

    def close(self) -> None:
        self._connection.close()

    def create_session(
        self,
        workspace: Path,
        goal: str,
        instructions: Sequence[str],
    ) -> SessionRecord:
        session_id = str(uuid.uuid4())
        normalized_workspace = _normalize_workspace(workspace)
        now = _utc_now()
        instructions_json = self._redact(
            json.dumps(tuple(instructions), ensure_ascii=False)
        )
        payload_json = self._redact(
            json.dumps(
                {"goal": goal, "workspace": normalized_workspace},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO sessions(
                        session_id, workspace, goal, status, created_at, updated_at,
                        instructions_json
                    ) VALUES (?, ?, ?, 'running', ?, ?, ?)
                    """,
                    (
                        session_id,
                        normalized_workspace,
                        self._redact(goal),
                        now,
                        now,
                        instructions_json,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO events(
                        session_id, sequence, event_type, payload_version,
                        payload_json, created_at
                    ) VALUES (?, 1, 'session_created', 1, ?, ?)
                    """,
                    (session_id, payload_json, now),
                )
        except sqlite3.Error as error:
            raise SessionStorageError(f"无法创建 Session: {error}") from error
        return self.get_session(session_id)

    def resume_session(
        self,
        workspace: Path,
        session_id: str | None = None,
    ) -> SessionRecord:
        normalized_workspace = _normalize_workspace(workspace)
        if session_id is None:
            row = self._connection.execute(
                """
                SELECT * FROM sessions
                WHERE workspace = ? AND status != 'completed'
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (normalized_workspace,),
            ).fetchone()
        else:
            row = self._connection.execute(
                """
                SELECT * FROM sessions
                WHERE workspace = ? AND session_id = ? AND status != 'completed'
                """,
                (normalized_workspace, session_id),
            ).fetchone()
        if row is None:
            raise SessionStorageError("当前工作区没有可恢复的 Session")
        record = _row_to_session(row)
        self._reconcile_incomplete_tool_calls(record.session_id)
        self._append_raw_event(
            record.session_id,
            "session_resumed",
            {"previous_status": record.status},
            status="running",
        )
        return self.get_session(record.session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        row = self._connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise SessionStorageError(f"Session 不存在: {session_id}")
        return _row_to_session(row)

    def list_sessions(self, workspace: Path) -> tuple[SessionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM sessions
            WHERE workspace = ?
            ORDER BY updated_at DESC, created_at DESC
            """,
            (_normalize_workspace(workspace),),
        ).fetchall()
        return tuple(_row_to_session(row) for row in rows)

    def append_event(self, session_id: str, event: AgentEvent) -> None:
        status: SessionStatus | None = None
        if isinstance(event, RunCompleted):
            status = "completed"
        elif isinstance(event, RunFailed):
            if event.stop_reason == "interrupted":
                status = "interrupted"
            else:
                status = "failed"
        payload = asdict(event)
        self._append_raw_event(
            session_id,
            _event_type(event),
            payload,
            status=status,
            model_event=event,
        )

    def load_history(self, session_id: str) -> tuple[Message, ...]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM events
            WHERE session_id = ? AND event_type IN ('model_completed', 'tool_completed')
            ORDER BY sequence
            """,
            (session_id,),
        ).fetchall()
        messages: list[Message] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            payload = payload["message"]
            if payload is None:
                continue
            messages.append(
                Message(
                    role=payload["role"],
                    content=payload["content"],
                    tool_call_id=payload["tool_call_id"],
                    tool_calls=tuple(
                        ToolCall(
                            id=call["id"],
                            name=call["name"],
                            arguments_json=call["arguments_json"],
                        )
                        for call in payload["tool_calls"]
                    ),
                )
            )
        return tuple(messages)

    def load_observed_evidence(self, session_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM events
            WHERE session_id = ? AND event_type = 'tool_completed'
            ORDER BY sequence
            """,
            (session_id,),
        ).fetchall()
        evidence: list[str] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if payload["status"] != "success":
                continue
            for path in payload.get("evidence_paths", ()):
                if path not in evidence:
                    evidence.append(path)
        return tuple(evidence)

    def load_verification_state(
        self,
        session_id: str,
    ) -> Literal["clean", "required", "failed"]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM events
            WHERE session_id = ? AND event_type = 'tool_completed'
            ORDER BY sequence
            """,
            (session_id,),
        ).fetchall()
        state: Literal["clean", "required", "failed"] = "clean"
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if payload["tool_name"] == "run_command" and payload["command_purpose"] == "verify":
                if payload["status"] == "error":
                    state = "failed"
                elif payload["changed_paths"]:
                    state = "required"
                else:
                    state = "clean"
            elif payload["changed_paths"] or (
                payload["tool_name"] == "run_command"
                and payload["status"] == "success"
                and payload["command_purpose"] in {"build", "modify"}
            ):
                state = "required"
        return state

    def _append_raw_event(
        self,
        session_id: str,
        event_type: str,
        payload: object,
        *,
        status: SessionStatus | None = None,
        model_event: AgentEvent | None = None,
    ) -> None:
        now = _utc_now()
        payload_json = self._redact(
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        try:
            with self._connection:
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise SessionStorageError(f"Session 不存在: {session_id}")
                sequence = int(row[0])
                self._connection.execute(
                    """
                    INSERT INTO events(
                        session_id, sequence, event_type, payload_version,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (session_id, sequence, event_type, payload_json, now),
                )
                if isinstance(model_event, ModelStarted):
                    attempt_row = self._connection.execute(
                        """
                        SELECT COALESCE(MAX(attempt), 0) + 1
                        FROM model_attempts WHERE session_id = ?
                        """,
                        (session_id,),
                    ).fetchone()
                    attempt = int(attempt_row[0]) if attempt_row is not None else 1
                    self._connection.execute(
                        """
                        INSERT INTO model_attempts(
                            session_id, attempt, event_sequence, status, created_at
                        ) VALUES (?, ?, ?, 'started', ?)
                        """,
                        (session_id, attempt, sequence, now),
                    )
                elif isinstance(model_event, ModelCompleted):
                    self._connection.execute(
                        """
                        UPDATE model_attempts
                        SET status = 'completed', input_tokens = ?, output_tokens = ?
                        WHERE session_id = ? AND attempt = (
                            SELECT MAX(attempt) FROM model_attempts WHERE session_id = ?
                        )
                        """,
                        (
                            model_event.usage.input_tokens,
                            model_event.usage.output_tokens,
                            session_id,
                            session_id,
                        ),
                    )
                elif isinstance(model_event, RunFailed):
                    self._connection.execute(
                        """
                        UPDATE model_attempts SET status = 'failed'
                        WHERE session_id = ? AND status = 'started'
                        """,
                        (session_id,),
                    )
                next_status = status
                if next_status is None:
                    current = self._connection.execute(
                        "SELECT status FROM sessions WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                    if current is None:
                        raise SessionStorageError(f"Session 不存在: {session_id}")
                    next_status = current[0]
                updated = self._connection.execute(
                    """
                    UPDATE sessions SET status = ?, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (next_status, now, session_id),
                )
                if updated.rowcount != 1:
                    raise SessionStorageError(f"Session 不存在: {session_id}")
        except sqlite3.Error as error:
            raise SessionStorageError(f"无法追加 Session 事件: {error}") from error

    def _reconcile_incomplete_tool_calls(self, session_id: str) -> None:
        incomplete = self._find_incomplete_tool_calls(session_id)
        for item in incomplete:
            if not item.started or item.call.name in READ_ONLY_TOOLS:
                self._append_recovery_tool_result(
                    session_id,
                    item,
                    "recovery_retry_required",
                    "上次进程未保存工具结果；请重新发起这个工具调用",
                )
                continue
            if item.call.name in APPROVAL_TOOLS and not item.approved:
                self._append_recovery_tool_result(
                    session_id,
                    item,
                    "approval_expired",
                    "上次一次性审批未形成可证明结果，已失效；如需执行请重新申请",
                )
                continue
            self._append_raw_event(
                session_id,
                "session_resume_blocked",
                {
                    "call_id": item.call.id,
                    "tool_name": item.call.name,
                    "reason": "outcome_unknown",
                },
                status="interrupted",
            )
            raise SessionOutcomeUnknownError(
                f"工具 {item.call.name} ({item.call.id}) 可能已产生副作用但结果未持久化；"
                "请先检查工作区，再决定如何处理"
            )

    def _append_recovery_tool_result(
        self,
        session_id: str,
        item: _IncompleteToolCall,
        error_code: str,
        message: str,
    ) -> None:
        tool_message = Message(
            role="tool",
            content=json.dumps(
                {"error_code": error_code, "message": message},
                ensure_ascii=False,
                sort_keys=True,
            ),
            tool_call_id=item.call.id,
        )
        self.append_event(
            session_id,
            ToolCompleted(
                call_id=item.call.id,
                tool_name=item.call.name,
                status="error",
                target=item.target,
                error_code=error_code,
                message=tool_message,
            ),
        )

    def _find_incomplete_tool_calls(
        self,
        session_id: str,
    ) -> tuple[_IncompleteToolCall, ...]:
        rows = self._connection.execute(
            """
            SELECT event_type, payload_json FROM events
            WHERE session_id = ?
            ORDER BY sequence
            """,
            (session_id,),
        ).fetchall()
        calls: list[ToolCall] = []
        completed: set[str] = set()
        started: dict[str, str] = {}
        approved: set[str] = set()
        for row in rows:
            event_type = str(row["event_type"])
            payload = json.loads(str(row["payload_json"]))
            if event_type == "model_completed" and payload.get("message") is not None:
                for call in payload["message"]["tool_calls"]:
                    calls.append(
                        ToolCall(
                            id=call["id"],
                            name=call["name"],
                            arguments_json=call["arguments_json"],
                        )
                    )
            elif event_type == "tool_started":
                started[payload["call_id"]] = payload["target"]
            elif event_type == "approval_decided" and payload["decision"] == "allow_once":
                approved.add(payload["call_id"])
            elif event_type == "tool_completed":
                completed.add(payload["call_id"])
        return tuple(
            _IncompleteToolCall(
                call=call,
                target=started.get(call.id, call.name),
                started=call.id in started,
                approved=call.id in approved,
            )
            for call in calls
            if call.id not in completed
        )

    def _initialize_schema(self) -> None:
        current_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > SCHEMA_VERSION:
            raise SessionStorageError(
                f"数据库版本 {current_version} 高于当前支持版本 {SCHEMA_VERSION}"
            )
        if current_version == SCHEMA_VERSION:
            return
        try:
            with self._connection:
                self._connection.executescript(
                    """
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
        except sqlite3.Error as error:
            raise SessionStorageError(f"无法初始化 Session 数据库: {error}") from error


def _row_to_session(row: sqlite3.Row) -> SessionRecord:
    instructions = json.loads(str(row["instructions_json"]))
    return SessionRecord(
        session_id=str(row["session_id"]),
        workspace=str(row["workspace"]),
        goal=str(row["goal"]),
        status=cast(SessionStatus, row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        instructions=tuple(str(value) for value in instructions),
    )


def _normalize_workspace(workspace: Path) -> str:
    return os.path.normcase(str(workspace.resolve(strict=True)))


def _event_type(event: AgentEvent) -> str:
    name = type(event).__name__
    characters: list[str] = []
    for index, character in enumerate(name):
        if character.isupper() and index:
            characters.append("_")
        characters.append(character.lower())
    return "".join(characters)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
