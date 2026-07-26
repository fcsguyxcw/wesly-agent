import json
from pathlib import Path
from typing import Any, Protocol

import httpx
import pytest
from openai import APIConnectionError
from openai.types.chat import ChatCompletion

from wesly.deepseek import DeepSeekAdapter
from wesly.model import Message, ModelProviderError, ModelRequest, ModelTurn, Usage


class RecordingCompletions:
    def __init__(self, response: ChatCompletion) -> None:
        self._response = response
        self.request: dict[str, Any] | None = None

    def create(self, **request: Any) -> ChatCompletion:
        self.request = request
        return self._response


class CompletionsClient(Protocol):
    def create(self, **request: Any) -> ChatCompletion: ...


class RecordingOpenAIClient:
    def __init__(self, completions: CompletionsClient) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


def test_adapter_maps_a_sanitized_deepseek_response() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "deepseek" / "direct_answer.json"
    response = ChatCompletion.model_validate(json.loads(fixture_path.read_text("utf-8")))
    completions = RecordingCompletions(response)
    adapter = DeepSeekAdapter(
        client=RecordingOpenAIClient(completions),
        model="deepseek-v4-pro",
    )

    turn = adapter.complete(
        ModelRequest(messages=(Message(role="user", content="这是什么项目？"),))
    )

    assert turn == ModelTurn(
        content="这是一个本地 Coding Agent。",
        tool_calls=(),
        finish_reason="stop",
        usage=Usage(input_tokens=15, output_tokens=9),
    )
    assert completions.request == {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": "这是什么项目？"}],
        "stream": False,
        "extra_body": {"thinking": {"type": "disabled"}},
    }


class FailingCompletions:
    def create(self, **request: Any) -> ChatCompletion:
        raise APIConnectionError(request=httpx.Request("POST", "https://api.deepseek.com"))


def test_adapter_maps_sdk_errors_to_a_safe_provider_error() -> None:
    adapter = DeepSeekAdapter(
        client=RecordingOpenAIClient(FailingCompletions()),
        model="deepseek-v4-pro",
    )

    with pytest.raises(ModelProviderError, match="无法连接模型服务"):
        adapter.complete(
            ModelRequest(messages=(Message(role="user", content="检查项目"),))
        )
