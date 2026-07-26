from wesly.context import DirectAnswerContextBuilder
from wesly.model import Message, ModelBudget, ModelRequest


def test_direct_answer_builder_uses_the_stable_model_request() -> None:
    builder = DirectAnswerContextBuilder()

    request = builder.build("这个项目是什么？")

    assert request == ModelRequest(
        instructions=(
            "You are Wesly, a local personal coding agent. "
            "Answer in the user's language.",
        ),
        messages=(Message(role="user", content="这个项目是什么？"),),
        tools=(),
        budget=ModelBudget(),
    )
