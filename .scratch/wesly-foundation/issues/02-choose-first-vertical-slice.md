# Choose the first real vertical slice

Type: grilling
Status: resolved
Blocked by: none

## Question

第一个可交付切片应使用哪个真实 Python 仓库场景，并以什么用户可观察行为和测试作为通过条件，才能既尽早自用又暴露核心 Agent 循环问题？

## Answer

第一个纵向切片采用“只读仓库调查”：用户在真实 Python 仓库中通过 `wesly "<question>"` 提问，Wesly 使用 DeepSeek 在列目录、搜索文本和读取文件三个只读工具之间自主选择并完成多轮模型—工具循环，终端展示工具活动，最终给出带真实文件路径依据的回答。

该切片不修改文件，也不执行任意命令。自动化测试使用 fake 模型覆盖循环、工具结果回传、停止条件、路径边界和 provider 错误；人工验收在一个真实 Python 仓库中询问 CLI 入口及执行流程，要求引用存在的文件、有限步内停止、失败时给出明确错误且不泄露密钥。
