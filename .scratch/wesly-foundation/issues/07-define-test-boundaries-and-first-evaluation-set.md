# Define test boundaries and the first evaluation set

Type: grilling
Status: resolved
Blocked by: 02, 03

## Question

哪些模块边界必须可替换为 fake，哪些集成测试和真实仓库任务共同证明纵向切片有效，以及首批 10 个验收任务如何选择和记录？

## Answer

测试只 fake 不可控外部边界：`ModelClient`、时钟/ID、用户批准输入及真正外部服务。Agent 循环、参数/schema 校验、权限策略、路径解析、工具、临时目录中的真实文件系统及临时 SQLite 均使用真实实现。DeepSeek 适配器以脱敏响应 fixture 做合同测试；真实 API 只用于显式 live smoke/evaluation，不进入默认套件。

测试分四层：纯转换和规则的单元测试；fake 模型配合真实核心模块的组件/集成测试；以子进程执行 `wesly` 的 CLI 端到端测试；真实 DeepSeek 与真实仓库的 live smoke/evaluation。默认测试不依赖网络、API key、用户真实仓库、生产数据库或测试顺序。每个纵向切片必须同时通过默认测试和对应真实任务验收。

首个“只读仓库调查”切片的行为矩阵至少覆盖：直接最终回答；列目录；连续搜索/读取；单轮多工具；非法 JSON；未知工具；路径逃逸或链接越界；文件不存在/解码/读取失败；provider error；模型轮数或工具调用上限；Ctrl+C；以及最终答案缺少真实文件依据。每个失败路径断言无未授权操作、正确 `ToolResult`/`RunFailed`、可理解 CLI、正确事件与停止原因、无密钥泄露。

真实能力评估属于 Wesly v1 整体验收，不只是验证程序能否运行。它使用三类仓库：用户熟悉的个人 Python 仓库、用户不熟悉的小型开源 Python 仓库、Wesly 自身的隔离副本。10 个任务固定为代码理解、Bug 修复、小型功能、重构和测试补充各 2 个；第一纵向切片只承担两个代码理解任务，其余在对应能力切片进入前冻结。

每个任务在正式运行前固定仓库与 commit、prompt、setup、成功条件、禁止变更、验证命令和时间/token/循环预算。单次正式尝试记录 `pass`、`fail`、`blocked` 或 `invalid`；网络/provider 暂时故障可以新增 attempt，但不能删除失败记录。v1 要求至少 8/10、每类至少通过 1 个、两个理解任务都通过，并对未批准副作用、凭据泄露、审计缺失及依赖用户手改代码实行零容忍。修复 Wesly 后的重跑必须使用新版本和新 run，不覆盖旧结果。

任务规格以 `evals/tasks/*.json` 版本控制，运行记录写入 `evals/runs/<run-id>/`，保存 Wesly/model 版本、Session/attempt、逐项检查、预算与用量、diff/验证/日志资产和人工审查，不保存密钥或无关代码。任务规格进入正式评估后冻结；修改必须产生新版本，旧结果继续保留。
