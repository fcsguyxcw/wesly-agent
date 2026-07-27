# 19 — 经逐次审批运行命令

**What to build:** Agent 可以请求执行测试、构建或其他本地命令，但每条命令都必须把真实可执行程序、参数、目标和原因展示给用户，并只执行一次被批准的精确操作。

**Blocked by:** 18 — 执行前重新验证已批准操作

**Status:** resolved

- [x] 命令优先表示为可执行程序和参数，完整 PowerShell 脚本按脚本原文审批。
- [x] 每次命令执行都请求审批，不因名称类似测试或构建而自动允许。
- [x] 工作目录、参数或环境效果变化后不能复用旧审批。
- [x] 拒绝、启动失败、非零退出、超时和中断产生可区分结果。
- [x] 命令输出受到大小限制并通过显式分页或安全截断标记呈现。
- [x] 组件和 CLI 测试使用受控命令证明允许、拒绝和无越权执行。

## Implementation result

- 新增 `run_command` 工具：`argv` 模式直接向操作系统传递已解析的可执行程序和参数且固定 `shell=False`；`powershell` 模式把完整脚本原文展示给用户，并通过 Windows PowerShell 的 `-Command` 一次执行。
- 规范化审批绑定可执行文件绝对路径及 SHA-256、完整参数或脚本、工作目录、环境覆盖、超时和原因；执行前重新解析并计算同一指纹，任一变化都不能复用旧批准。
- 每次执行消费一个待批准对象；直接执行、拒绝和重放旧对象均返回 `permission_denied`，同一命令再次运行也必须重新请求批准。
- 结构化区分 `command_start_failed`、`command_nonzero`、`command_timeout` 和 `command_termination_failed`；执行中收到中断会终止完整进程树、消费批准并由 Agent 报告 `interrupted`。
- stdout 与 stderr 分别最多返回 12 KiB，并携带 `total_bytes`、`returned_bytes` 和 `truncated`；敏感环境值在审批展示和命令输出中替换为 `[REDACTED]`，完整工具结果保持在 32 KiB 内。
- 组件测试覆盖允许、拒绝、审批重放、参数/工作目录/环境漂移、启动失败、非零退出、超时、中断、截断、脱敏和 PowerShell 原文；Agent 测试证明两次命令产生两次审批，CLI 测试通过真实受控 Python 命令证明批准后仅执行一次。
- Windows 命令以挂起状态启动，先加入启用 `KILL_ON_JOB_CLOSE` 的 Job Object，再恢复主线程；超时和中断使用 `TerminateJobObject` 清理完整派生树。POSIX 命令使用新 session，并通过 `killpg` 清理完整进程组。
- Job 创建、绑定或恢复失败时，挂起进程会被直接终止且返回 `command_start_failed`；树级终止调用失败时不再声称普通超时，而返回 `command_termination_failed`。
- 真实父子进程回归测试关闭继承管道后延迟写入副作用文件，证明超时和中断返回后孙进程不会继续运行；另有测试证明 Job 绑定失败时命令主体从未执行。
- 完整测试为 `109 passed, 4 skipped`，严格 mypy 检查通过。
