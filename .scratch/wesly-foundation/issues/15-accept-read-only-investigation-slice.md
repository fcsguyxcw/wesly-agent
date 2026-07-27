# 15 — 完成只读仓库调查切片验收

**What to build:** 用户获得一个可稳定日常试用的只读仓库调查 Agent：它能在明确预算和循环限制内调查、引用和回答，在失败或中断时给出准确原因，并通过首片完整行为矩阵。

**Blocked by:** 13 — 完成搜索、读取与文件证据闭环；14 — 加载并固定作用域化 WESLY.md

**Status:** resolved

- [x] `chronological-v1` 按顺序构建当前任务上下文，不注入其他任务或未读文件。
- [x] 输入预算为 56K、输出保留 8K；超限前不调用供应商并报告 `context_limit`。
- [x] 达到模型轮次、工具调用或供应商错误时产生准确停止原因和非成功结果。
- [x] Ctrl+C 安全中断，默认和详细输出均不泄露密钥、隐藏思考或未截断大结果。
- [x] 首片规定的单元、组件、CLI 端到端和适配器契约测试全部通过。
- [x] 两个冻结的仓库理解任务能够按记录规则执行并保存结果。

## Implementation result

- `ReadOnlyContextBuilder` 现在固定使用 `chronological-v1`，包含创建时工作区/Git 快照，按当前任务和本 Session 历史顺序构建请求，并以保守 ASCII/非 ASCII 估算在 56K 输入前停止；`ModelRequest` 为供应商保留 8K 输出。
- Agent 在模型调用前把超限映射为 `context_limit`，并把模型或工具阶段的 Ctrl+C 映射为 `interrupted`；CLI 返回 130，`--verbose` 只显示安全事件标识、结束原因和 usage。
- 分页工具定义明确要求使用 `next_cursor`；文件证据只解析普通正文引用，忽略代码块中的引用语法示例，但普通未观察文件引用仍以 `evidence_error` 失败。
- 两个版本化理解任务分别固定 Wesly `6dcb15b7...` 与 ItsDangerous `672971d6...`。正式 live 记录均为 `pass`，目标 HEAD 未变且工作区干净；最大单次输入分别为 14,704 和 21,385 tokens，未触发 70% 上下文证据门。
- 严格类型检查通过；完整测试为 `54 passed, 2 skipped`。跳过项是当前 Windows 权限不允许创建文件或目录符号链接，普通路径越界和解析路径授权测试已通过。
