# Define trusted project instruction sources and precedence

Type: grilling
Status: resolved
Blocked by: 08

## Question

Wesly v1 应从哪些明确位置加载项目级指令，用户、system、项目文件与工具数据之间采用什么优先级、作用域、信任和版本审计规则，才能支持真实仓库约定而不让任意文件内容提升为指令？

## Answer

Wesly v1 只自动加载 `%USERPROFILE%\.wesly\WESLY.md` 与工作区内精确命名的 `WESLY.md`；不自动加载 `AGENTS.md`、`CLAUDE.md`、`CODEX.md`、README、代码注释、Git 消息或其他 Agent 记忆。需要兼容文件时以后通过显式配置提供，不默认混用。

文件内容不会无边界拼接。ContextBuilder 将内置规则、用户全局指令和每个项目指令包装为带来源、作用域、内容哈希和边界的结构化区块，再序列化到 DeepSeek system message；加载清单、哈希及版本进入 Session 审计。项目指令只能影响工作方式，不能创建权限、声明批准或覆盖确定性安全规则。

目录作用域从用户全局、项目根到目标文件最近目录逐级具体化。优先级为：Wesly 内置安全/运行不变量；当前用户明确要求；最近目录 `WESLY.md`；逐级上层 `WESLY.md`；项目根 `WESLY.md`；用户全局 `WESLY.md`；普通工具数据。多文件操作分别计算目标文件的适用链。

每个 Session 创建时一次性扫描、读取并保存全部适用 `WESLY.md` 的路径、作用域、内容与哈希，整个 Session（包括恢复）固定使用该快照。磁盘文件后续创建、修改或删除都不热更新；只提示当前 Session 仍使用旧快照。Agent 修改 `WESLY.md` 后也必须创建新 Session 才生效，不提供 reload。

扫描不跟随越出工作区的 symlink/junction，跳过 `.git`、`.venv`、`venv`、`node_modules`、`__pycache__`、`.tox`、`build`、`dist`，只接受普通 UTF-8 文本。单文件最多 16 KiB，全部指令最多 32 KiB并计入上下文预算；超过时 Session 创建以 `instructions_limit` 失败并列出文件，不能静默截断或部分加载。
