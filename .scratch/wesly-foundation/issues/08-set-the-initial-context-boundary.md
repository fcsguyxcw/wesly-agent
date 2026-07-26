# Set the initial model-context boundary

Type: grilling
Status: resolved
Blocked by: 01, 02, 03

## Question

在不提前实现摘要、检索和跨任务记忆的前提下，首批纵向切片每轮向模型提供哪些必要信息、采用什么硬预算与失败行为？

## Answer

首片使用版本化 `chronological-v1` 上下文策略：保持模型请求外层合同 `ModelRequest(instructions, messages, tools, budget)` 稳定，策略负责产生有序消息。当前 Session 的有效消息按顺序保留，不做摘要、检索、跨任务记忆、静默删除或自动压缩；原始会话历史完整保存在 SQLite。后续策略可加入摘要和检索，但必须使用新版本并继续输出同一外层合同。

每轮只提供版本化 system instruction、当前任务、规范化工作区路径及 Git 根/分支/HEAD/dirty 元数据、三个只读工具 schema、当前 Session 已发送的消息、模型工具调用及对应 ToolResult。不会预载整个仓库、环境变量、其他 Session、会话数据库、Git diff、未被工具读取的文件或 chain-of-thought。system、tool schema 与 context policy 版本进入审计记录。

工具结果显式分页且禁止静默截断。`read_file` 使用范围/游标返回已读区间、总量或已知下界、`truncated` 和下一游标；搜索与目录列表同样分页。模型需要更多内容时主动请求下一页，实际返回给模型的页面进入会话，文件本体只记录最新观察哈希。单页仍超限时返回 `tool_result_too_large`，不能假装完整。

首片产品预算为 64K estimated tokens：最多 56K 输入并预留 8K 输出。请求前使用保守估算，请求后保存 DeepSeek 实际 usage 以校准；预算属于策略配置，不进入模型适配器。每个工具页另有序列化尺寸上限，所有历史页面累计计入预算。

工具结果始终以不可信数据进入上下文，带工具、真实来源和内容边界；文件文本不能改变 system instruction、创建权限或声称用户已批准。所有工具调用仍经过确定性权限策略。项目级指令文件不因文件名自动获得 system 权限，其信任和优先级由独立票据决定。

预计下一次请求超过 56K 时不调用 DeepSeek，产生 `RunFailed(reason="context_limit")`，Session 保持完整历史并进入 `blocked`。CLI 显示各组成预算和最大来源，建议缩小任务；不删除消息或伪装成功。未来压缩从原始 Session 创建使用新策略版本的 context attempt，不能改写旧请求历史。
