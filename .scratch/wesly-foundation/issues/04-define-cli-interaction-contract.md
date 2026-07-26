# Define the minimum CLI interaction contract

Type: prototype
Status: resolved
Blocked by: 02

## Question

`wesly`、`wesly "<task>"`、任务查看与恢复在首个可用版本中应如何交互，哪些进度、工具调用、批准请求和最终结果必须对用户可见？

## Prototype asset

[Wesly CLI interaction prototype](../prototypes/cli-interaction/README.md)

## Answer

正式首版采用可滚动的活动记录，不采用全屏状态仪表盘。原型顶部状态字段和手动动作仅用于暴露状态机；正式 CLI 显示用户输入、简洁的模型活动、工具名称与安全目标、成功或失败状态、批准提示和最终结果，不展示 chain-of-thought、完整模型请求/响应、文件全文、密钥或未经截断的冗长输出。

入口合同为：

- `wesly`：在当前工作区创建交互式新任务。
- `wesly "<task>"`：直接创建并执行新任务。
- `wesly sessions`：列出当前工作区的历史任务。
- `wesly resume`：恢复当前工作区最近一个未完成任务。
- `wesly resume <session-id>`：恢复指定任务。
- `Ctrl+C`：安全中断；持久化能力存在时显示准确的恢复命令。

启动 `wesly` 默认创建新任务，不自动注入或恢复旧任务。第一纵向切片只实现前两个入口；任务列表和恢复在会话持久化切片实现，但遵循上述命令合同。

默认工具活动形如 `→ read_file src/wesly/cli.py` 与 `✓ 已读取 2.4 KB`。`--verbose` 可增加事件类型、调用 ID、耗时、token 用量和安全截断后的工具结果，但任何模式均不显示凭据或思考内容。

首版批准提示显示操作、参数、安全目标、原因和工作区，只提供“本次允许”与“拒绝”。拒绝生成结构化工具结果并允许模型调整，不自动判定整个任务失败；持久化信任规则与会话级放行不在本票中引入。

结束输出必须明确区分完成与未完成：成功时包含最终回答、文件路径依据和简短运行统计；失败或达到限制时包含停止原因、最后动作、建议，以及仅在确实可恢复时显示的恢复命令。具体颜色和排版可调整，但这些信息不可缺失。
