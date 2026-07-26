"""Portable state machine for the throwaway Wesly CLI prototype."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class Phase(StrEnum):
    IDLE = "idle"
    MODEL = "model_running"
    TOOL = "tool_running"
    APPROVAL = "awaiting_approval"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class State:
    phase: Phase = Phase.IDLE
    session_id: str = "—"
    entry_mode: str = "—"
    task: str = "—"
    current_action: str = "等待任务"
    approval_reason: str = "—"
    final_result: str = "—"
    model_turns: int = 0
    tool_calls: int = 0
    events: tuple[str, ...] = ()


@dataclass(frozen=True)
class Action:
    kind: str


def transition(state: State, action: Action) -> State:
    kind = action.kind

    if kind in {"start_direct", "start_interactive"}:
        mode = "wesly \"<task>\"" if kind == "start_direct" else "wesly"
        return State(
            phase=Phase.MODEL,
            session_id="demo-001",
            entry_mode=mode,
            task="解释这个项目的 CLI 入口和执行流程",
            current_action="正在请求模型决定下一步",
            model_turns=1,
            events=(f"任务已创建 · {mode}", "模型开始分析"),
        )

    if kind == "request_tool" and state.phase == Phase.MODEL:
        return replace(
            state,
            phase=Phase.TOOL,
            current_action="read_file · src/wesly/cli.py",
            tool_calls=state.tool_calls + 1,
            events=state.events + ("模型请求工具 · read_file", "开始读取 · src/wesly/cli.py"),
        )

    if kind == "tool_finished" and state.phase == Phase.TOOL:
        return replace(
            state,
            phase=Phase.MODEL,
            current_action="工具完成，正在请求模型继续",
            model_turns=state.model_turns + 1,
            events=state.events + ("读取完成 · 2.4 KB", "模型继续分析"),
        )

    if kind == "request_approval" and state.phase == Phase.MODEL:
        return replace(
            state,
            phase=Phase.APPROVAL,
            current_action="等待用户批准",
            approval_reason="run_command · pytest（模拟后续切片）",
            events=state.events + ("需要批准 · run_command pytest",),
        )

    if kind in {"approve", "deny"} and state.phase == Phase.APPROVAL:
        decision = "已批准" if kind == "approve" else "已拒绝"
        return replace(
            state,
            phase=Phase.MODEL,
            current_action=f"{decision}，正在通知模型",
            approval_reason="—",
            model_turns=state.model_turns + 1,
            events=state.events + (f"用户{decision}", "模型继续分析"),
        )

    if kind == "complete" and state.phase == Phase.MODEL:
        return replace(
            state,
            phase=Phase.COMPLETED,
            current_action="任务完成",
            final_result="CLI 入口位于 src/wesly/cli.py，main() 创建 Agent 并启动运行循环。",
            events=state.events + ("模型给出最终回答", "任务完成"),
        )

    if kind == "fail" and state.phase in {Phase.MODEL, Phase.TOOL, Phase.APPROVAL}:
        return replace(
            state,
            phase=Phase.FAILED,
            current_action="provider_error · DeepSeek 服务暂时不可用",
            events=state.events + ("任务失败 · provider_error",),
        )

    if kind == "interrupt" and state.phase in {Phase.MODEL, Phase.TOOL, Phase.APPROVAL}:
        return replace(
            state,
            phase=Phase.INTERRUPTED,
            current_action="已中断，可恢复",
            events=state.events + ("用户中断任务",),
        )

    if kind == "resume" and state.phase == Phase.INTERRUPTED:
        return replace(
            state,
            phase=Phase.MODEL,
            current_action="已恢复，正在请求模型继续",
            model_turns=state.model_turns + 1,
            events=state.events + ("任务已恢复", "模型继续分析"),
        )

    return replace(state, events=state.events + (f"忽略无效操作 · {kind}",))
