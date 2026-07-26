from typing import Protocol

from wesly.model import Message, ModelBudget, ModelRequest


class ContextBuilder(Protocol):
    def build(self, task: str) -> ModelRequest: ...


class ChronologicalV1ContextBuilder:
    policy_version = "chronological-v1"

    def build(self, task: str) -> ModelRequest:
        return ModelRequest(
            instructions=(
                "You are Wesly, a local personal coding agent. "
                "Answer in the user's language.",
            ),
            messages=(Message(role="user", content=task),),
            tools=(),
            budget=ModelBudget(input_tokens=56_000, output_tokens=8_000),
        )
