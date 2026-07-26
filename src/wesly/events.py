from dataclasses import dataclass

from wesly.model import Usage


@dataclass(frozen=True, slots=True)
class ModelStarted:
    turn: int


@dataclass(frozen=True, slots=True)
class ModelCompleted:
    turn: int
    finish_reason: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class RunCompleted:
    answer: str
    model_turns: int
    tool_calls: int
    usage: Usage


@dataclass(frozen=True, slots=True)
class RunFailed:
    stop_reason: str
    message: str
    model_turns: int
    tool_calls: int


AgentEvent = ModelStarted | ModelCompleted | RunCompleted | RunFailed
