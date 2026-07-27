# Wesly

Wesly 是一个面向个人长期使用的本地 Coding Agent。第一个只读仓库调查切片已经完成：CLI 可以调用 DeepSeek，并允许模型安全地分页列目录、搜索文本和读取文件范围。最终答案用 `[[工作区相对路径]]` 标记文件引用，且引用必须来自本次运行的真实搜索或读取证据。上下文采用 `chronological-v1`，单次请求限制为 56K 输入并预留 8K 输出；超限会在调用模型前明确停止。尚未提供文件修改、命令执行或 Session 持久化。

## 环境

- Windows
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- 已设置 `DEEPSEEK_API_KEY`

## 安装与运行

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv sync --python 3.12
uv run wesly "解释这个项目"
```

需要查看安全的事件 ID、结束原因和单轮 token usage 时使用：

```powershell
uv run wesly --verbose "解释这个项目"
```

默认模型是 `deepseek-v4-pro`。如需切换官方支持的模型：

```powershell
$env:WESLY_MODEL = 'deepseek-v4-flash'
uv run wesly "解释这个项目"
```

## 验证

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv run --python 3.12 mypy src
uv run --python 3.12 pytest -q
```

两个冻结的真实仓库理解任务及通过记录位于 `evals/`。
