# 14 — 加载并固定作用域化 WESLY.md

**What to build:** 新任务启动时，Wesly 从明确允许的全局和工作区位置加载适用的 `WESLY.md`，按目录作用域和优先级组装指令快照，并在该任务运行期间保持不变。

**Blocked by:** 13 — 完成搜索、读取与文件证据闭环

**Status:** resolved

- [x] 只识别约定的全局文件和工作区内精确命名的 `WESLY.md`。
- [x] 指令按内置安全、当前用户、最近目录、祖先、根和全局顺序生效。
- [x] 每个指令区块带来源、作用域和内容哈希，普通工具数据不能提升为指令。
- [x] Session 内使用创建时快照；磁盘文件变化不热重载，新 Session 才读取新版本。
- [x] 扫描拒绝工作区外链接，跳过约定目录，并校验 UTF-8、单文件和总大小限制。
- [x] 任一指令超限时整体以 `instructions_limit` 失败，不部分注入或静默截断。

## Implementation result

- `InstructionSnapshot` 在 `ReadOnlyContextBuilder` 创建时一次性扫描用户全局和工作区 `WESLY.md`，按目录深度排序，并保存来源、作用域、SHA-256 与内容。
- 结构化指令区块只进入 system instructions；内置安全、当前用户请求和工具数据的不可信边界保持明确，作用域规则仅适用于对应目录树。
- 扫描跳过约定目录和目录链接，拒绝越出工作区的指令链接、非普通文件与非 UTF-8 内容；大小在读取前按 16 KiB 单文件和 32 KiB 总量校验。
- 旧 builder 始终复用创建时快照，新 builder 才观察磁盘变更；超限由 CLI 在调用模型前以 `instructions_limit` 明确失败。
- 严格类型检查通过；完整测试为 `43 passed, 2 skipped`。跳过项是当前 Windows 权限不允许创建文件或目录符号链接，普通路径越界和解析路径授权测试已通过。
