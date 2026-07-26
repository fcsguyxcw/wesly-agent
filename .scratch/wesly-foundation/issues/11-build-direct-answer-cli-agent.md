# 11 — 建立可直接回答的 CLI Agent

**What to build:** 用户运行 `wesly "任务"` 后，Wesly 通过隔离供应商格式的模型边界向 DeepSeek 发起请求，在可滚动活动日志中呈现运行状态，并输出直接文本答案。这是后续工具循环的最小可运行骨架，但本票不提供任何本地工具。

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] Python 3.12 项目可安装，并提供 `wesly` 命令。
- [x] `wesly "任务"` 通过 Wesly 自己的模型请求和模型轮次类型完成一次直接回答。
- [x] DeepSeek SDK、响应字段和错误格式只在模型适配器中出现，密钥只从 `DEEPSEEK_API_KEY` 读取。
- [x] CLI 显示模型开始、模型完成和运行完成或失败，不显示密钥或隐藏思考。
- [x] 成功输出包含最终答案和基础运行统计；供应商失败产生明确非零退出状态。
- [x] 以脚本化 `ModelClient` 验证真实 Agent 循环，以 CLI 子进程验证用户可见行为，并以脱敏 fixture 验证 DeepSeek 映射。
- [x] 默认测试不访问网络、不需要真实 API key，也不伪造 Agent 内部模块。

## Implementation result

- 实现提交：`399d488`、`1006c7b`、`1a91643`、`8978ccf`。
- 严格类型检查通过，14 个自动化测试通过。
- 真实 DeepSeek smoke 返回 `WESLY_SMOKE_OK`，活动、完成状态、答案和 token 统计均正常显示。
- Standards 与 Spec 双轴最终复审均无发现。
