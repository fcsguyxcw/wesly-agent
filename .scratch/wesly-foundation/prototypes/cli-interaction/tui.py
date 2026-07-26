"""PROTOTYPE: manually drive Wesly's minimum CLI interaction states."""

from __future__ import annotations

from state import Action, State, transition


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
CLEAR = "\x1b[2J\x1b[H"


def field(name: str, value: object) -> str:
    return f"{BOLD}{name:<16}{RESET} {value}"


def render(state: State) -> None:
    print(CLEAR, end="")
    print(f"{BOLD}WESLY CLI INTERACTION — THROWAWAY PROTOTYPE{RESET}\n")
    print(field("session", state.session_id))
    print(field("entry", state.entry_mode))
    print(field("task", state.task))
    print(field("phase", state.phase.value))
    print(field("current action", state.current_action))
    print(field("approval", state.approval_reason))
    print(field("model turns", state.model_turns))
    print(field("tool calls", state.tool_calls))
    print(field("final result", state.final_result))

    print(f"\n{BOLD}Visible activity{RESET}")
    for event in state.events[-8:]:
        print(f"  {DIM}-{RESET} {event}")
    if not state.events:
        print(f"  {DIM}No activity yet{RESET}")

    print(f"\n{BOLD}Actions{RESET}")
    print("[1] direct task   [2] interactive task   [t] request tool")
    print("[o] tool finished   [a] request approval   [y] approve   [n] deny")
    print("[f] final answer   [e] provider error   [i] interrupt   [r] resume   [q] quit")


def main() -> None:
    state = State()
    actions = {
        "1": "start_direct",
        "2": "start_interactive",
        "t": "request_tool",
        "o": "tool_finished",
        "a": "request_approval",
        "y": "approve",
        "n": "deny",
        "f": "complete",
        "e": "fail",
        "i": "interrupt",
        "r": "resume",
    }

    while True:
        render(state)
        key = input("\n> ").strip().lower()
        if key == "q":
            return
        state = transition(state, Action(actions.get(key, key or "empty")))


if __name__ == "__main__":
    main()
