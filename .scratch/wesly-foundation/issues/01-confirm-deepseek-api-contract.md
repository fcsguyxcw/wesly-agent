# Confirm the DeepSeek API contract needed by Wesly

Type: research
Status: resolved
Blocked by: none

## Question

DeepSeek 当前官方 API 对 OpenAI 兼容调用、工具调用、流式输出、错误与限流、上下文和 token 计量分别提供什么可靠合同；Wesly 的首个模型适配器可以依赖哪些能力，哪些差异必须隔离？

## Research asset

[DeepSeek API contract findings](../../../docs/research/deepseek-api-contract.md)

## Answer

Wesly 首个适配器可依赖稳定的 OpenAI-compatible Chat Completions、非流式函数工具调用、SSE 文本流和响应内 token 计量；必须隔离当前模型名、思考模式及 `reasoning_content` 回传、缓存计量、保活/错误与重试语义。默认不依赖即将弃用的模型别名、Beta strict/FIM/prefix 能力或未被官方流式 schema 明确保证的工具调用分片。逐项证据和待验证边界见 [研究记录](../../../docs/research/deepseek-api-contract.md)。
