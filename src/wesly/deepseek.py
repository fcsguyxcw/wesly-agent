from __future__ import annotations

from typing import Any

from openai import APIConnectionError, OpenAI, OpenAIError

from wesly.model import Message, ModelRequest, ModelTurn, ToolCall, Usage
from wesly.model import ModelProviderError


class DeepSeekAdapter:
    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    def complete(self, request: ModelRequest) -> ModelTurn:
        provider_request: dict[str, Any] = {
            "model": self._model,
            "messages": (
                [
                    {"role": "system", "content": instruction}
                    for instruction in request.instructions
                ]
                + [_provider_message(message) for message in request.messages]
            ),
            "stream": False,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if request.budget.output_tokens is not None:
            provider_request["max_tokens"] = request.budget.output_tokens
        if request.tools:
            provider_request["tools"] = list(request.tools)

        try:
            response = self._client.chat.completions.create(**provider_request)
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


def _provider_message(message: Message) -> dict[str, Any]:
    provider_message: dict[str, Any] = {
        "role": message.role,
        "content": message.content,
    }
    if message.tool_calls:
        provider_message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments_json,
                },
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        provider_message["tool_call_id"] = message.tool_call_id
    return provider_message
