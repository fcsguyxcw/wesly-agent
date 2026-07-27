from collections.abc import Iterator

from wesly.context import (
    ContextBuilder,
    ContextLimitError,
    DirectAnswerContextBuilder,
)
from wesly.evidence import extract_file_citations
from wesly.events import (
    AgentEvent,
    ModelCompleted,
    ModelStarted,
    RunCompleted,
    RunFailed,
    ToolCompleted,
    ToolStarted,
)
from wesly.model import Message, ModelClient, ModelProviderError, Usage
from wesly.tools import ToolRegistry


class Agent:
    def __init__(
        self,
        model_client: ModelClient,
        context_builder: ContextBuilder | None = None,
        tool_registry: ToolRegistry | None = None,
        max_model_turns: int = 12,
        max_tool_calls: int = 30,
    ) -> None:
        self._model_client = model_client
        self._context_builder = context_builder or DirectAnswerContextBuilder()
        self._tool_registry = tool_registry
        self._max_model_turns = max_model_turns
        self._max_tool_calls = max_tool_calls

    def run(self, task: str) -> Iterator[AgentEvent]:
        history: list[Message] = []
        tool_calls = 0
        usage = Usage(input_tokens=0, output_tokens=0)
        observed_evidence: set[str] = set()

        for turn in range(1, self._max_model_turns + 1):
            try:
                request = self._context_builder.build(task, history)
            except ContextLimitError as error:
                yield RunFailed(
                    stop_reason="context_limit",
                    message=str(error),
                    model_turns=turn - 1,
                    tool_calls=tool_calls,
                )
                return
            yield ModelStarted(turn=turn)
            try:
                model_turn = self._model_client.complete(request)
            except KeyboardInterrupt:
                yield RunFailed(
                    stop_reason="interrupted",
                    message="任务已由用户中断",
                    model_turns=turn - 1,
                    tool_calls=tool_calls,
                )
                return
            except ModelProviderError as error:
                yield RunFailed(
                    stop_reason="provider_error",
                    message=str(error),
                    model_turns=turn,
                    tool_calls=tool_calls,
                )
                return

            usage = Usage(
                input_tokens=usage.input_tokens + model_turn.usage.input_tokens,
                output_tokens=usage.output_tokens + model_turn.usage.output_tokens,
            )
            yield ModelCompleted(
                turn=turn,
                finish_reason=model_turn.finish_reason,
                usage=model_turn.usage,
            )
            history.append(
                Message(
                    role="assistant",
                    content=model_turn.content,
                    tool_calls=model_turn.tool_calls,
                )
            )

            if model_turn.tool_calls:
                if self._tool_registry is None:
                    yield RunFailed(
                        stop_reason="internal_error",
                        message="Agent 尚未配置工具注册表",
                        model_turns=turn,
                        tool_calls=tool_calls,
                    )
                    return
                for call in model_turn.tool_calls:
                    if tool_calls >= self._max_tool_calls:
                        yield RunFailed(
                            stop_reason="tool_limit",
                            message="已达到工具调用上限",
                            model_turns=turn,
                            tool_calls=tool_calls,
                        )
                        return
                    target = self._tool_registry.describe_target(call)
                    yield ToolStarted(
                        call_id=call.id,
                        tool_name=call.name,
                        target=target,
                    )
                    try:
                        result = self._tool_registry.execute(call)
                    except KeyboardInterrupt:
                        yield RunFailed(
                            stop_reason="interrupted",
                            message="任务已由用户中断",
                            model_turns=turn,
                            tool_calls=tool_calls,
                        )
                        return
                    tool_calls += 1
                    observed_evidence.update(result.evidence_paths)
                    yield ToolCompleted(
                        call_id=result.call_id,
                        tool_name=result.tool_name,
                        status=result.status,
                        target=result.target,
                        error_code=result.error_code,
                    )
                    history.append(
                        Message(
                            role="tool",
                            content=result.content,
                            tool_call_id=result.call_id,
                        )
                    )
                continue

            if not model_turn.content:
                yield RunFailed(
                    stop_reason="provider_error",
                    message="模型响应不包含答案",
                    model_turns=turn,
                    tool_calls=tool_calls,
                )
                return
            citations = extract_file_citations(model_turn.content)
            missing_evidence = tuple(
                path for path in citations if path not in observed_evidence
            )
            if missing_evidence:
                yield RunFailed(
                    stop_reason="evidence_error",
                    message=(
                        "模型引用了本次运行未观察的文件: "
                        + ", ".join(missing_evidence)
                    ),
                    model_turns=turn,
                    tool_calls=tool_calls,
                )
                return
            yield RunCompleted(
                answer=model_turn.content,
                model_turns=turn,
                tool_calls=tool_calls,
                usage=usage,
                evidence_paths=citations,
            )
            return

        yield RunFailed(
            stop_reason="turn_limit",
            message="已达到模型轮次上限",
            model_turns=self._max_model_turns,
            tool_calls=tool_calls,
        )
