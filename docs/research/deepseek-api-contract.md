# DeepSeek API contract for Wesly

调查日期：2026-07-22。本文只依据 DeepSeek 官方 API 文档与官方变更记录；未进行任何认证请求。

## 结论

Wesly 首个模型适配器可以依赖 DeepSeek 的 OpenAI-compatible Chat Completions 接口、非流式函数工具调用、SSE 文本流、结构化停止原因与响应内 token 用量。它不能把“OpenAI 兼容”理解成完全等价：DeepSeek 的模型名、思考模式字段、思考内容回传规则、失效参数、缓存计量、keep-alive 和错误分类都应封装在适配器内部。

建议首版使用稳定端点 `https://api.deepseek.com/chat/completions`，默认模型采用 `deepseek-v4-pro`，但把模型名作为配置而非领域常量。不要以即将弃用的 `deepseek-chat` 或 `deepseek-reasoner` 作为默认值。工具调用先走非流式响应；文本可流式展示。流式工具调用在当前官方 Chat Completion 流式 schema 中没有明确列出 `delta.tool_calls`，在未经后续认证契约测试前不应把它当成已确认能力。

## 1. OpenAI 兼容调用与模型端点

- DeepSeek 官方声明 API 格式兼容 OpenAI；OpenAI 格式的 `base_url` 是 `https://api.deepseek.com`，聊天接口是 `POST /chat/completions`。官方 Python 示例直接使用 OpenAI SDK 的 `client.chat.completions.create(...)`。[Your First API Call](https://api-docs.deepseek.com/guides/function_calling/)
- 当前稳定模型 ID 是 `deepseek-v4-flash` 和 `deepseek-v4-pro`。`deepseek-chat` 与 `deepseek-reasoner` 只是分别映射到 flash 的非思考/思考模式，并将在 2026-07-24 15:59 UTC 弃用。[Your First API Call](https://api-docs.deepseek.com/guides/function_calling/) [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- V4 Flash 与 Pro 均支持思考/非思考模式、JSON Output 和 Tool Calls；官方列出的上下文长度均为 1M，最大输出为 384K。[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- `https://api.deepseek.com/beta` 用于 strict tool calls、Chat Prefix Completion 和 FIM 等 Beta 能力，不应成为首版常规聊天端点。[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)

**适配器边界：** `base_url`、`model`、思考模式和额外请求字段属于 provider 配置。Wesly 内部不应让 Agent 循环依赖 DeepSeek 模型别名或 OpenAI SDK 对象。

## 2. 工具调用合同

- 请求使用 OpenAI 风格的 `tools` 数组；当前只支持 `type: "function"`，最多提供 128 个函数。`tool_choice` 支持 `none`、`auto`、`required` 或指定函数。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)
- 模型返回 `message.tool_calls[]`，每项包含 `id`、函数名和字符串形式的 `arguments`。执行结果以 `role: "tool"`、原 `tool_call_id` 和 `content` 回传；模型本身不执行工具。[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)
- 官方明确警告：普通模式下 `arguments` 不保证是合法 JSON，也可能包含 schema 未定义的参数。因此 Wesly 必须解析并校验参数，校验成功后才进入权限检查和执行，不能把模型输出直接当函数参数。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)
- `strict: true` 能约束输出符合受支持的 JSON Schema，但属于 Beta，要求 beta base URL，而且只支持官方列出的 schema 子集。首版不应靠 strict mode 代替本地校验。[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)
- `finish_reason` 可能为 `tool_calls`；也可能为 `stop`、`length`、`content_filter` 或 `insufficient_system_resource`。Agent 循环必须按枚举处理，不能仅用“是否有文本”判断是否结束。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)

## 3. 思考模式的 DeepSeek 特有合同

- V4 默认启用思考模式。OpenAI SDK 中 `thinking` 需通过 `extra_body={"thinking": {"type": ...}}` 传递，推理强度使用 `reasoning_effort`；这是“兼容但不相同”的直接例子。[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- 思考内容在 `reasoning_content` 中，与最终 `content` 分离。思考模式不支持 `temperature`、`top_p`、`presence_penalty`、`frequency_penalty`；其中部分字段即使传入也不会报错，只是不生效。[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- 如果某个思考轮次发生工具调用，后续请求必须完整回传该 assistant 消息的 `reasoning_content`，否则 API 返回 400。没有工具调用的旧思考内容无需回传，传入也会被忽略。[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)

**适配器边界：** 内部统一消息必须能够保留 provider-specific 元数据，至少包括 `reasoning_content`；但 UI、会话审计和 Agent 策略不应直接依赖该字段。首版可显式关闭思考模式来降低协议复杂度，或启用后完整实现上述回传规则，不能只取 `content`。

## 4. 流式输出合同

- `stream: true` 返回 `text/event-stream`，由增量 chat completion chunks 组成，以 `data: [DONE]` 结束；文本位于 `delta.content`，思考内容位于 `delta.reasoning_content`。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)
- 设置 `stream_options.include_usage: true` 时，`[DONE]` 前有一个额外 chunk：其 `choices` 为空，`usage` 是整个请求的统计；其他 chunk 的 `usage` 为 `null`。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)
- 排队期间，非流式响应可能持续返回空行，流式响应可能持续返回 SSE 注释 `: keep-alive`。自写解析器必须忽略这些保活数据；若 10 分钟仍未开始推理，服务端会关闭连接。[Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/)
- 当前官方流式 schema 明确列出 `delta.content` 和 `delta.reasoning_content`，但没有列出 `delta.tool_calls`；官方工具指南也只给出非流式工具示例。因此本文不能确认“流式工具参数分片”的稳定合同。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/) [Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)

**首版依赖：** 流式输出只用于文本/思考展示；工具循环使用非流式响应。以后若要流式组装工具调用，先加入针对真实 API 的独立契约测试。

## 5. 错误、限流与重试

官方列出的 HTTP 错误为：

| 状态 | 官方含义 | 首版处理 |
|---|---|---|
| 400 | 请求格式错误；思考工具轮缺失 `reasoning_content` 也会触发 | 不自动重试；转成协议错误并保留安全的错误详情 |
| 401 | 认证失败 | 不重试；提示配置凭据，不记录密钥 |
| 402 | 余额不足 | 不重试；提示账户状态 |
| 422 | 参数无效 | 不重试；转成协议/配置错误 |
| 429 | 超过并发/速率限制 | 可按有上限的指数退避重试 |
| 500 | 服务端错误 | 短暂等待后有限重试 |
| 503 | 服务过载 | 短暂等待后有限重试 |

来源：[Error Codes](https://api-docs.deepseek.com/quick_start/error_codes/)

当前模型的并发限制按账户而非 API key 计算：`deepseek-v4-pro` 为 500，`deepseek-v4-flash` 为 2500；请求从发出到响应完成一直占用一个并发位，超限返回 429。[Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/)

官方页面没有在这里承诺 `Retry-After` 响应头、错误 JSON body 的稳定 schema、幂等键或精确退避时长。因此重试策略必须由 Wesly 控制，并区分：

- 请求尚未获得任何响应的传输失败；
- 明确可重试的 429/500/503；
- 流已经产生部分输出后断开，此时自动重发可能重复文本或工具调用，默认不得透明重试。

## 6. 上下文与 token 计量

- 输入 token 与生成 token 总和受模型上下文长度限制；当前 V4 官方规格为 1M context、最大输出 384K。客户端仍应显式设置合理的 `max_tokens`，并处理 `finish_reason: "length"`。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/) [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- 非流式响应的 `usage` 包含 `prompt_tokens`、`completion_tokens`、`total_tokens`、`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`，以及 `completion_tokens_details.reasoning_tokens`。`prompt_tokens` 等于 cache hit 与 miss 两部分之和。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)
- 官方说明字符到 token 只能粗略估计，实际计量以响应的 `usage` 为准；官方另提供离线 tokenizer 资源。[Token & Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/)
- Context Caching 默认开启，不需改 API；命中依赖可复用前缀，属 best-effort，不保证命中。应记录 hit/miss 供成本观测，但不能把缓存命中当正确性条件。[Context Caching](https://api-docs.deepseek.com/guides/kv_cache)

## 7. 首个适配器应暴露的内部合同

建议把 provider 响应归一化为以下概念，而不是向上层泄漏 OpenAI SDK 类型：

1. `ModelRequest`：内部消息、工具 schema、是否流式、输出预算与 provider options。
2. `ModelEvent`：文本增量、思考增量、完成消息、用量或 provider 错误。
3. `AssistantTurn`：`content`、零到多个 `tool_calls`、`finish_reason`、不透明 provider metadata。
4. `Usage`：prompt、completion、total、cache hit/miss、reasoning tokens；字段允许缺失，尤其在未请求流式 usage 时。
5. `ProviderError`：认证、余额、请求无效、限流、服务端、传输中断；携带 `retryable`，但不携带或输出密钥。

必须隔离的 DeepSeek 差异：

- 当前模型 ID 与弃用别名；
- `thinking` 的 `extra_body` 传法和 `reasoning_effort`；
- `reasoning_content` 的保存与工具轮回传；
- 思考模式下被忽略/不支持的采样参数；
- DeepSeek 特有的 cache hit/miss 和 reasoning token 计量；
- SSE keep-alive、10 分钟排队关闭，以及流中断不可透明重试；
- Beta base URL 与稳定 base URL 的能力隔离。

## 8. 已确认依赖、明确非依赖与待验证项

**首版可依赖**

- 稳定 OpenAI Chat Completions 端点；
- `deepseek-v4-pro` / `deepseek-v4-flash` 配置化选择；
- 非流式函数工具调用和 tool-result 回传；
- 非流式完整 token usage；
- SSE 文本流、`[DONE]`、可选最终 usage chunk；
- 以状态码和 `finish_reason` 驱动的显式状态处理。

**首版不依赖**

- `deepseek-chat` / `deepseek-reasoner` 旧别名；
- beta strict mode、FIM 或 Chat Prefix Completion；
- 模型生成的工具参数天然合法；
- context cache 必然命中；
- 所有 OpenAI 参数在 DeepSeek 上语义相同；
- 流中断后自动重发仍保持 exactly-once 工具语义。

**需要后续认证契约测试，但不阻塞规格阶段**

- 流式工具调用的实际 chunk 形状与多工具参数拼接；
- 错误响应 body、响应头及 SDK 异常类型的实际形状；
- DeepSeek 当前服务与选定 OpenAI Python SDK 版本的具体兼容矩阵；
- 思考模式开关与编码任务效果/成本的产品取舍。
