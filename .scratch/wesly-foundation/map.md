# Wesly foundation decision map

Label: wayfinder:map

## Destination

形成一套可直接交给实现阶段的 Wesly 产品与架构规格，以及按纵向切片排列的构建路线；所有阻碍首批真实 Python 仓库任务验收的前置决策均已明确。

## Notes

- 产品：Windows 本地单用户 CLI/TUI Coding Agent，命令与 Python 包名均为 `wesly`。
- 核心任务：完成仓库理解、计划、修改、命令执行、验证迭代和交付说明的闭环。
- 自主边界：受监督的自主执行；用户负责架构取舍与代码审查，Codex 负责实现、测试、文档和验证。
- 技术约束：Python 3.12、DeepSeek API（`DEEPSEEK_API_KEY`）、模块化单体、自研核心 Agent 运行时。
- 构建方式：纵向切片，每个切片保持可运行，并在用户审查通过后进入下一片。
- 安全与可靠性：统一分级工具权限；任务会话可持久化、恢复和审计。
- 首个可用版本验收：在至少 3 个真实 Python 仓库中完成 10 个任务，覆盖代码理解、功能实现、Bug 修复、重构和测试；生成可审查改动与验证结果，不依赖用户手动改代码完成任务。
- 领域语言见 [CONTEXT.md](../../CONTEXT.md)；已确认的高成本决策见 [ADRs](../../docs/adr/)。
- 决策会话使用 `grilling` 与 `domain-modeling`；外部事实调查使用 `research`。

## Decisions so far

<!-- Closed ticket links are appended here. -->

- DeepSeek 首个适配器依赖稳定 Chat Completions、非流式工具调用与可计量 SSE 文本流，并隔离思考模式、模型生命周期和 provider 错误语义（[票据](issues/01-confirm-deepseek-api-contract.md)）。
- [选择第一个真实纵向切片](issues/02-choose-first-vertical-slice.md) — 首片采用只读仓库调查，以三个只读工具、多轮循环、文件依据和有限步停止完成真实 Python 仓库验收。
- [定义运行时事件与工具合同](issues/03-define-runtime-events-and-tool-contract.md) — DeepSeek 只在适配器边界出现，Agent 使用五组最小内部类型、未信任工具参数、结构化事件与有硬限制的显式循环。
- [定义最小 CLI 交互合同](issues/04-define-cli-interaction-contract.md) — 正式 CLI 使用简洁滚动活动、显式新建/恢复命令、一次性批准和诚实的完成/未完成结果，详细诊断通过 `--verbose` 提供。
- [选择会话存储与恢复语义](issues/05-choose-session-storage-and-recovery-contract.md) — 用户本地 SQLite 保存追加事件和可更新摘要，以动态文件/Git 观察、独占租约及结果可证明性驱动安全恢复。
- [定义工具权限分类](issues/06-define-tool-permission-classification.md) — 权限基于不可变实际操作及可复验前置状态，所有 Shell 命令逐次批准，文件操作按目标/敏感性/影响分级并在异常时 fail closed。
- [定义测试边界和首批评估集](issues/07-define-test-boundaries-and-first-evaluation-set.md) — 默认套件 fake 外部边界但运行真实核心模块，v1 以版本化三仓十任务按 8/10 门槛验收且安全违规零容忍。
- [确定初始模型上下文边界](issues/08-set-the-initial-context-boundary.md) — `chronological-v1` 以稳定请求合同、显式分页和 56K+8K 预算提供当前会话数据，工具内容不可信且超限诚实进入 blocked。
- [定义可信项目指令来源与优先级](issues/10-define-trusted-project-instructions.md) — v1 仅加载有目录作用域的 `WESLY.md`，在 Session 创建时固定带哈希快照，并以明确优先级和大小边界进入 system message。
- [冻结实现路线](issues/09-freeze-the-implementation-roadmap.md) — v1 按只读调查、可审查修改、验证循环、持久恢复、产品硬化和能力验收六片推进，每片经用户审查后解锁下一片。

## Not yet specified

<!-- No unresolved decisions remain within the v1 destination. -->

## Out of scope

- 首版不包含桌面 GUI、Web UI、云端执行或远程沙箱。
- 首版不包含多用户、账号系统、多 Agent 协作或第三方插件市场。
- 首版不包含浏览器控制、通用桌面自动化、自动部署或 CI 平台集成。
- 首版不训练或微调模型，也不承诺 Python 以外语言的专项支持。
- 自动上下文摘要/检索不属于默认 v1 路线；仅在 70% 预算证据门触发时阻塞发布，否则进入 v1.1。
- 跨任务工作区记忆、Memory MCP、通用 MCP 客户端与第三方扩展协议在 v1 后重新立项。
- 高级 TUI 布局、快捷键和复杂 diff/approval 体验在最小滚动 CLI 获得真实反馈后重新立项。
