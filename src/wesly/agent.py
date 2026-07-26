from collections.abc import Iterator

from wesly.context import ContextBuilder, DirectAnswerContextBuilder
from wesly.events import AgentEvent, ModelCompleted, ModelStarted, RunCompleted, RunFailed
from wesly.model import ModelClient, ModelProviderError


class Agent:
    def __init__(
        self,
        model_client: ModelClient,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._model_client = model_client
        self._context_builder = context_builder or DirectAnswerContextBuilder()

    def run(self, task: str) -> Iterator[AgentEvent]:
        turn = 1
        yield ModelStarted(turn=turn)

        try:
            model_turn = self._model_client.complete(self._context_builder.build(task))
        except ModelProviderError as error:
            yield RunFailed(
                stop_reason="provider_error",
                message=str(error),
                model_turns=turn,
                tool_calls=0,
            )
            return

        yield ModelCompleted(
            turn=turn,
            finish_reason=model_turn.finish_reason,
            usage=model_turn.usage,
        )
        if not model_turn.content:
            yield RunFailed(
                stop_reason="provider_error",
                message="模型响应不包含答案",
                model_turns=turn,
                tool_calls=0,
            )
            return
        yield RunCompleted(
            answer=model_turn.content,
            model_turns=turn,
            tool_calls=0,
            usage=model_turn.usage,
        )
