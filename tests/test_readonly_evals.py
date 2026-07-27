import json
from pathlib import Path
from typing import Any, cast

from wesly.context import INPUT_TOKEN_BUDGET, OUTPUT_TOKEN_BUDGET


def test_two_read_only_understanding_tasks_are_frozen_and_bounded() -> None:
    tasks_directory = Path(__file__).parents[1] / "evals" / "tasks"
    task_files = sorted(tasks_directory.glob("understanding-*-v1.json"))

    assert [path.name for path in task_files] == [
        "understanding-itsdangerous-timed-url-safe-v1.json",
        "understanding-wesly-readonly-flow-v1.json",
    ]
    tasks = [
        cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        for path in task_files
    ]
    assert {task["id"] for task in tasks} == {
        "understanding-itsdangerous-timed-url-safe",
        "understanding-wesly-readonly-flow",
    }
    for task in tasks:
        assert task["version"] == 1
        assert task["category"] == "understanding"
        assert len(task["repository"]["commit"]) == 40
        assert task["prompt"].endswith("不要修改任何文件。")
        assert task["success_criteria"]
        assert task["forbidden_changes"]
        assert task["limits"] == {
            "input_tokens": INPUT_TOKEN_BUDGET,
            "output_tokens": OUTPUT_TOKEN_BUDGET,
            "model_turns": 12,
            "tool_calls": 30,
            "wall_clock_seconds": 600,
        }


def test_read_only_result_template_uses_the_fixed_status_vocabulary() -> None:
    template_path = Path(__file__).parents[1] / "evals" / "result-template.json"
    template = cast(
        dict[str, Any],
        json.loads(template_path.read_text(encoding="utf-8")),
    )

    assert template["schema_version"] == 1
    assert template["status"] in {"pass", "fail", "blocked", "invalid"}
    assert template["stdout_file"] == "stdout.txt"
    assert template["stderr_file"] == "stderr.txt"
    assert "api_key" not in json.dumps(template).lower()


def test_both_frozen_understanding_tasks_have_a_passing_record() -> None:
    runs_directory = Path(__file__).parents[1] / "evals" / "runs"
    results = [
        cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(runs_directory.glob("*/result.json"))
    ]

    assert {result["task_id"] for result in results} == {
        "understanding-itsdangerous-timed-url-safe",
        "understanding-wesly-readonly-flow",
    }
    assert all(result["status"] == "pass" for result in results)
    assert all(result["target_head_unchanged"] for result in results)
    assert all(result["target_worktree_clean"] for result in results)
    assert all(all(check["passed"] for check in result["checks"]) for result in results)
    assert all(result["evidence_paths"] for result in results)
