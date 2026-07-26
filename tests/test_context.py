from wesly.context import ChronologicalV1ContextBuilder
from wesly.model import Message, ModelBudget, ModelRequest


def test_chronological_v1_builds_the_stable_model_request() -> None:
    builder = ChronologicalV1ContextBuilder()

    request = builder.build("这个项目是什么？")

    assert builder.policy_version == "chronological-v1"
    assert request == ModelRequest(
        instructions=(
            "You are Wesly, a local personal coding agent. "
            "Answer in the user's language.",
        ),
        messages=(Message(role="user", content="这个项目是什么？"),),
        tools=(),
        budget=ModelBudget(input_tokens=56_000, output_tokens=8_000),
    )
