from __future__ import annotations

from typing import Any

from openai import APIConnectionError, OpenAI, OpenAIError

from wesly.model import ModelRequest, ModelTurn, ToolCall, Usage
from wesly.model import ModelProviderError


class DeepSeekAdapter:
    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    def complete(self, request: ModelRequest) -> ModelTurn:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=(
                    [
                        {"role": "system", "content": instruction}
                        for instruction in request.instructions
                    ]
                    + [
                        {"role": message.role, "content": message.content}
                        for message in request.messages
                    ]
                ),
                stream=False,
                max_tokens=request.budget.output_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except APIConnectionError as error:
            raise ModelProviderError("无法连接模型服务") from error
        except OpenAIError as error:
            raise ModelProviderError("模型服务请求失败") from error

        if not response.choices or response.usage is None:
            raise ModelProviderError("模型服务返回了无法解析的响应")

        choice = response.choices[0]
        provider_usage = response.usage
        usage = Usage(
            input_tokens=provider_usage.prompt_tokens,
            output_tokens=provider_usage.completion_tokens,
        )
        tool_calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments_json=call.function.arguments,
            )
            for call in (choice.message.tool_calls or ())
        )
        return ModelTurn(
            content=choice.message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "unknown",
            usage=usage,
        )


def create_deepseek_adapter(*, api_key: str, model: str) -> DeepSeekAdapter:
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        max_retries=0,
    )
    return DeepSeekAdapter(client=client, model=model)
