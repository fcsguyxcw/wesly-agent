import json
import shutil
import sys
import time
from pathlib import Path
from typing import cast

import pytest

import wesly.commands
import wesly.process_tree
from wesly.model import ToolCall
from wesly.permissions import PreparedOperation
from wesly.process_tree import ProcessTree, ProcessTreeError
from wesly.tools import ToolRegistry


def command_call(
    *,
    args: list[str],
    cwd: str = ".",
    env: dict[str, str] | None = None,
    timeout_seconds: int = 10,
    reason: str = "运行受控测试命令",
    purpose: str = "inspect",
    call_id: str = "command",
) -> ToolCall:
    return ToolCall(
        call_id,
        "run_command",
        json.dumps(
            {
                "mode": "argv",
                "executable": sys.executable,
                "args": args,
                "cwd": cwd,
                "env": env or {},
                "timeout_seconds": timeout_seconds,
                "reason": reason,
                "purpose": purpose,
            },
            ensure_ascii=False,
        ),
    )


def prepare(registry: ToolRegistry, call: ToolCall) -> PreparedOperation:
    operation = registry.prepare_operation(call)
    assert isinstance(operation, PreparedOperation)
    return operation


def payload(result_content: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(result_content))


def test_command_requires_fresh_approval_for_every_execution(tmp_path: Path) -> None:
    marker = tmp_path / "runs.txt"
    call = command_call(
        args=[
            "-c",
            "from pathlib import Path; Path('runs.txt').open('a', encoding='utf-8').write('x')",
        ]
    )
    registry = ToolRegistry(tmp_path)

    denied = registry.execute(call)
    assert denied.error_code == "permission_denied"
    assert not marker.exists()

    first_approval = prepare(registry, call)
    first = registry.execute(call, approved_operation=first_approval)
    assert first.status == "success"
    assert marker.read_text(encoding="utf-8") == "x"

    replay = registry.execute(call, approved_operation=first_approval)
    assert replay.error_code == "permission_denied"
    assert marker.read_text(encoding="utf-8") == "x"

    second_approval = prepare(registry, call)
    second = registry.execute(call, approved_operation=second_approval)
    assert second.status == "success"
    assert marker.read_text(encoding="utf-8") == "xx"


def test_command_reports_ordinary_workspace_changes_and_structured_purpose(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry(tmp_path)
    call = command_call(
        args=["-c", "from pathlib import Path; Path('generated.txt').write_text('x')"],
        purpose="modify",
    )

    result = registry.execute(call, approved_operation=prepare(registry, call))
    data = payload(result.content)

    assert result.status == "success"
    assert result.changed_paths == ("generated.txt",)
    assert result.command_purpose == "modify"
    assert result.change_tracking_complete is True
    assert data["purpose"] == "modify"
    assert data["changed_paths"] == ["generated.txt"]
    assert data["change_tracking_complete"] is True


def test_command_marks_tracking_incomplete_for_sensitive_workspace_files(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry(tmp_path)
    call = command_call(
        args=["-c", "from pathlib import Path; Path('.env').write_text('TOKEN=x')"],
        purpose="modify",
    )

    result = registry.execute(call, approved_operation=prepare(registry, call))

    assert result.status == "success"
    assert result.changed_paths == ()
    assert result.change_tracking_complete is False
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "TOKEN=x"


@pytest.mark.parametrize("changed_field", ["args", "cwd", "env"])
def test_command_approval_cannot_be_reused_after_effect_change(
    tmp_path: Path,
    changed_field: str,
) -> None:
    (tmp_path / "other").mkdir()
    registry = ToolRegistry(tmp_path)
    original = command_call(args=["-c", "pass"])
    approved = prepare(registry, original)
    values = {
        "args": ["-c", "print('changed')"],
        "cwd": "other",
        "env": {"WESLY_FLAG": "changed"},
    }
    changed = command_call(
        args=cast(list[str], values["args"] if changed_field == "args" else ["-c", "pass"]),
        cwd=cast(str, values["cwd"] if changed_field == "cwd" else "."),
        env=cast(dict[str, str], values["env"] if changed_field == "env" else {}),
    )

    result = registry.execute(changed, approved_operation=approved)

    assert result.error_code == "permission_denied"
    original_result = registry.execute(original, approved_operation=approved)
    assert original_result.status == "success"


def test_command_result_distinguishes_nonzero_exit(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path)
    call = command_call(
        args=["-c", "import sys; print('out'); print('bad', file=sys.stderr); raise SystemExit(7)"]
    )

    result = registry.execute(call, approved_operation=prepare(registry, call))

    data = payload(result.content)
    assert result.status == "error"
    assert result.error_code == "command_nonzero"
    assert data["exit_code"] == 7
    assert cast(dict[str, object], data["stdout"])["text"] == "out\r\n"
    assert cast(dict[str, object], data["stderr"])["text"] == "bad\r\n"


def test_command_result_distinguishes_timeout_and_kills_process(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path)
    call = command_call(
        args=["-c", "import time; print('started', flush=True); time.sleep(30)"],
        timeout_seconds=1,
    )

    result = registry.execute(call, approved_operation=prepare(registry, call))

    data = payload(result.content)
    assert result.status == "error"
    assert result.error_code == "command_timeout"
    assert data["timed_out"] is True
    assert "started" in cast(dict[str, object], data["stdout"])["text"]


def test_command_timeout_terminates_descendant_processes(tmp_path: Path) -> None:
    child_source = (
        "import time; from pathlib import Path; "
        "Path('child-ready.txt').write_text('ready'); time.sleep(2); "
        "Path('escaped-side-effect.txt').write_text('escaped')"
    )
    parent_source = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_source!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(30)"
    )
    registry = ToolRegistry(tmp_path)
    call = command_call(args=["-c", parent_source], timeout_seconds=1)

    result = registry.execute(call, approved_operation=prepare(registry, call))

    assert result.error_code == "command_timeout"
    assert (tmp_path / "child-ready.txt").exists()
    time.sleep(2.2)
    assert not (tmp_path / "escaped-side-effect.txt").exists()


def test_command_start_failure_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ToolRegistry(tmp_path)
    call = command_call(args=["-c", "pass"])
    approved = prepare(registry, call)

    def fail_to_start(*args: object, **kwargs: object) -> object:
        raise OSError("simulated start failure")

    monkeypatch.setattr(wesly.commands.subprocess, "Popen", fail_to_start)
    result = registry.execute(call, approved_operation=approved)

    assert result.status == "error"
    assert result.error_code == "command_start_failed"
    assert "simulated start failure" not in result.content


def test_windows_job_assignment_failure_has_no_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows Job Object specific regression")

    def fail_assignment(job_handle: int, process_id: int) -> None:
        del job_handle, process_id
        raise ProcessTreeError("simulated assignment failure")

    monkeypatch.setattr(
        wesly.process_tree,
        "_assign_process_to_windows_job",
        fail_assignment,
    )
    registry = ToolRegistry(tmp_path)
    call = command_call(
        args=["-c", "from pathlib import Path; Path('must-not-exist.txt').write_text('x')"]
    )

    result = registry.execute(call, approved_operation=prepare(registry, call))

    assert result.error_code == "command_start_failed"
    assert not (tmp_path / "must-not-exist.txt").exists()


def test_command_reports_tree_termination_failure_without_claiming_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_spawn = wesly.commands.spawn_process_tree

    class FailingTerminationTree:
        def __init__(self, tree: ProcessTree) -> None:
            self.process = tree.process
            self._tree = tree

        def terminate(self) -> None:
            raise ProcessTreeError("simulated tree termination failure")

        def close(self) -> None:
            self._tree.close()

    def spawn_with_failed_termination(*args: object, **kwargs: object) -> FailingTerminationTree:
        return FailingTerminationTree(original_spawn(*args, **kwargs))

    monkeypatch.setattr(wesly.commands, "spawn_process_tree", spawn_with_failed_termination)
    registry = ToolRegistry(tmp_path)
    call = command_call(args=["-c", "import time; time.sleep(30)"], timeout_seconds=1)

    result = registry.execute(call, approved_operation=prepare(registry, call))

    assert result.status == "error"
    assert result.error_code == "command_termination_failed"
    assert payload(result.content)["error_code"] == "command_termination_failed"


def test_command_interrupt_kills_process_and_remains_distinguishable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_source = (
        "import time; from pathlib import Path; "
        "Path('interrupt-child-ready.txt').write_text('ready'); time.sleep(2); "
        "Path('interrupt-escaped.txt').write_text('escaped')"
    )
    parent_source = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_source!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(30)"
    )
    registry = ToolRegistry(tmp_path)
    call = command_call(args=["-c", parent_source])
    approved = prepare(registry, call)
    original_communicate = wesly.commands.subprocess.Popen.communicate
    first_call = True

    def interrupt_after_child_starts(
        process: wesly.commands.subprocess.Popen[bytes],
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        nonlocal first_call
        if first_call:
            first_call = False
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if (tmp_path / "interrupt-child-ready.txt").exists():
                    raise KeyboardInterrupt
                time.sleep(0.02)
            raise AssertionError("descendant process did not start")
        return original_communicate(process, input=input, timeout=timeout)

    monkeypatch.setattr(
        wesly.commands.subprocess.Popen,
        "communicate",
        interrupt_after_child_starts,
    )

    with pytest.raises(KeyboardInterrupt):
        registry.execute(call, approved_operation=approved)

    assert (tmp_path / "interrupt-child-ready.txt").exists()
    time.sleep(2.2)
    assert not (tmp_path / "interrupt-escaped.txt").exists()
    assert registry.execute(call, approved_operation=approved).error_code == "permission_denied"


def test_command_output_is_bounded_and_explicitly_marked(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path)
    call = command_call(
        args=[
            "-c",
            "import sys; print('o' * 40000); print('e' * 40000, file=sys.stderr)",
        ]
    )

    result = registry.execute(call, approved_operation=prepare(registry, call))

    data = payload(result.content)
    stdout = cast(dict[str, object], data["stdout"])
    stderr = cast(dict[str, object], data["stderr"])
    assert result.status == "success"
    assert stdout["truncated"] is True
    assert stderr["truncated"] is True
    assert stdout["total_bytes"] > stdout["returned_bytes"]
    assert stderr["total_bytes"] > stderr["returned_bytes"]
    assert len(result.content.encode("utf-8")) <= 32 * 1024


def test_command_environment_is_applied_but_sensitive_values_are_redacted(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry(tmp_path)
    secret = "command-secret-value"
    call = command_call(
        args=["-c", "import os; print(os.environ['WESLY_TEST_TOKEN'])"],
        env={"WESLY_TEST_TOKEN": secret},
    )
    approved = prepare(registry, call)

    result = registry.execute(call, approved_operation=approved)

    assert result.status == "success"
    assert secret not in approved.parameters
    assert secret not in result.content
    assert "[REDACTED]" in result.content


def test_command_output_redacts_inherited_sensitive_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "inherited-command-secret"
    monkeypatch.setenv("WESLY_HOST_SECRET", secret)
    registry = ToolRegistry(tmp_path)
    call = command_call(args=["-c", "import os; print(os.environ['WESLY_HOST_SECRET'])"])

    result = registry.execute(call, approved_operation=prepare(registry, call))

    assert result.status == "success"
    assert secret not in result.content
    assert "[REDACTED]" in result.content


def test_powershell_script_is_approved_as_complete_source(tmp_path: Path) -> None:
    if shutil.which("powershell.exe") is None:
        pytest.skip("当前环境没有 Windows PowerShell")
    marker = tmp_path / "powershell-result.txt"
    script = f"Set-Content -LiteralPath '{marker}' -Value 'done' -NoNewline"
    call = ToolCall(
        "powershell",
        "run_command",
        json.dumps(
            {
                "mode": "powershell",
                "powershell_script": script,
                "cwd": ".",
                "env": {},
                "timeout_seconds": 10,
                "reason": "运行 PowerShell 验证脚本",
                "purpose": "modify",
            },
            ensure_ascii=False,
        ),
    )
    registry = ToolRegistry(tmp_path)
    approved = prepare(registry, call)

    assert script in approved.parameters
    denied = registry.execute(call)
    assert denied.error_code == "permission_denied"
    assert not marker.exists()
    approved = prepare(registry, call)
    result = registry.execute(call, approved_operation=approved)

    assert result.status == "success"
    assert marker.read_text(encoding="utf-8") == "done"
