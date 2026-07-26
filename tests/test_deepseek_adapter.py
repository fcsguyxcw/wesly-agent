import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

import httpx
import pytest
from openai import APIConnectionError
from openai.types.chat import ChatCompletion

from wesly.context import DirectAnswerContextBuilder
from wesly.deepseek import DeepSeekAdapter
from wesly.model import ModelProviderError, ModelTurn, Usage


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

    turn = adapter.complete(DirectAnswerContextBuilder().build("这是什么项目？"))

    assert turn == ModelTurn(
        content="这是一个本地 Coding Agent。",
        tool_calls=(),
        finish_reason="stop",
        usage=Usage(input_tokens=15, output_tokens=9),
    )
    assert completions.request == {
        "model": "deepseek-v4-pro",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Wesly, a local personal coding agent. "
                    "Answer in the user's language."
                ),
            },
            {"role": "user", "content": "这是什么项目？"},
        ],
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
        adapter.complete(DirectAnswerContextBuilder().build("检查项目"))


def test_adapter_rejects_a_response_without_choices() -> None:
    response = ChatCompletion.model_validate(
        {
            "id": "chatcmpl-empty",
            "choices": [],
            "created": 1_753_200_000,
            "model": "deepseek-v4-pro",
            "object": "chat.completion",
            "usage": {
                "completion_tokens": 0,
                "prompt_tokens": 6,
                "total_tokens": 6,
            },
        }
    )
    adapter = DeepSeekAdapter(
        client=RecordingOpenAIClient(RecordingCompletions(response)),
        model="deepseek-v4-pro",
    )

    with pytest.raises(ModelProviderError, match="模型服务返回了无法解析的响应"):
        adapter.complete(DirectAnswerContextBuilder().build("检查项目"))


def test_direct_answer_adapter_rejects_tools_instead_of_dropping_them() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "deepseek" / "direct_answer.json"
    response = ChatCompletion.model_validate(json.loads(fixture_path.read_text("utf-8")))
    adapter = DeepSeekAdapter(
        client=RecordingOpenAIClient(RecordingCompletions(response)),
        model="deepseek-v4-pro",
    )
    request = replace(
        DirectAnswerContextBuilder().build("检查项目"),
        tools=({"type": "function"},),
    )

    with pytest.raises(ModelProviderError, match="当前切片尚未支持模型工具"):
        adapter.complete(request)
