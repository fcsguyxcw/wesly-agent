# Define the runtime event and tool contract

Type: grilling
Status: resolved
Blocked by: 01, 02

## Question

Agent、模型适配器、工具、权限和会话模块之间交换哪些最小且稳定的事件与结果类型，才能支持首个纵向切片而不提前设计完整框架？

## Answer

供应商格式只存在于 `DeepSeekAdapter`：它把 DeepSeek SDK 请求与响应转换为 Wesly 的内部类型，Agent、CLI、会话和除适配器外的测试均不依赖 SDK 类型。首片定义以下最小词汇：

- `ModelRequest(messages, available_tools)`：Agent 发给模型适配器的请求。
- `ModelTurn(content, tool_calls, finish_reason, usage)`：适配器返回的一轮归一化结果。
- `ToolCall(id, name, arguments_json)`：模型提出但尚未信任的工具调用；参数保留为原始 JSON 字符串。
- `ToolResult(call_id, tool_name, status, content, error_code)`：工具成功或预期失败的统一结果。
- `AgentEvent`：首片仅含 `ModelStarted`、`ModelCompleted`、`ToolStarted`、`ToolCompleted`、`RunCompleted` 与 `RunFailed`，由 CLI 渲染、会话保存、测试断言。

所有 `ToolCall` 依次经过工具存在性检查、JSON 解析、schema 校验、权限判断和执行；预期工具失败由执行器捕获并转成 `ToolResult`，编程错误和进程级故障不能伪装成普通工具失败。事件通过同步 iterator 或回调产生，不引入消息队列或事件总线。

Agent 使用显式循环，并以 `max_model_turns=12`、`max_tool_calls=30` 作为首片默认硬限制。运行只以 `completed`、`turn_limit`、`tool_limit`、`provider_error` 或 `internal_error` 结束，停止原因与计数通过终止事件报告。

首片明确关闭 DeepSeek 思考模式，因此当前内部消息不承载 `reasoning_content`；是否启用思考模式及如何保存不透明 provider 元数据，必须在核心只读循环稳定后基于真实任务另行决定。首片也不提前定义计划、审批、流式文本、摘要或跨任务记忆类型。
