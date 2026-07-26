# Choose session storage and recovery semantics

Type: grilling
Status: resolved
Blocked by: 03

## Question

首版任务会话应采用何种持久化格式、写入与恢复规则，才能在保持实现简单的同时可靠保存审计历史、检测工作区漂移并阻止并发写入？

## Answer

Wesly 使用 Python 标准库支持的 SQLite，数据库位于 `%LOCALAPPDATA%\Wesly\wesly.db`，不在代码仓库中创建会话文件。稳定查询字段存入 `sessions`，运行事实以带 `sequence`、`event_type`、`payload_json` 和版本的 `events` 记录；事件只追加，会话摘要可更新。追加事件与更新摘要必须位于同一事务。

Session 默认永久保留。完成、失败、中断、工作区移动或长期未使用都不会自动删除；只有用户通过 `wesly sessions delete <session-id>` 明确确认后，才在事务中删除 Session 与关联数据。敏感信息必须在持久化前过滤，不能依赖后续删除补救。

工作区漂移以动态观察为准，而非只保存会话启动快照：每次文件读取或修改成功后，对实际磁盘内容计算哈希并追加观察事件；历史保留所有版本，恢复比较每个文件最近一次成功观察的哈希。写入落盘后、哈希事件提交前崩溃会表现为漂移并安全暂停。Git 基线同时记录仓库根、HEAD commit、可空分支名、dirty 状态及规范化 `git status --porcelain=v2` 的指纹；恢复结合 Git 状态与接触文件哈希判断相关变化，不能自动覆盖用户的会话外修改。

同一 Session 使用带随机 `owner_id`、PID、心跳和过期时间的独占租约；只读查询不获取租约，其他 Session 不受影响。过期租约只能在明确提示上一进程可能异常退出后接管，并在接管后先进入 `interrupted`、重新执行漂移与恢复检查，再进入 `running`。

未完成操作按结果可证明性恢复：

- `safe_retry`：只读或明确幂等操作可自动重试。
- `reconcilable`：通过前置状态、后置条件、幂等键或外部查询证明结果；已成功则补记完成，明确未生效则在前置条件仍成立时重试。
- `outcome_unknown`：无法证明结果时才要求用户决定。

模型请求重试必须创建新 attempt 并记录额外成本。首个只读切片只实现 `safe_retry`；引入副作用工具时实现各自的协调策略。

数据库使用 `PRAGMA user_version`，事件 payload 独立带版本。启动时仅执行事务式向前迁移，非简单迁移前备份；失败时回滚。旧版 Wesly 遇到更高版本数据库时拒绝写入，未知事件字段必须保留。
