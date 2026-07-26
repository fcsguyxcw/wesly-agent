# Wesly

Wesly 是一个面向个人长期使用的本地 Coding Agent。当前完成的是第一个纵向切片：通过 CLI 调用 DeepSeek 并输出直接回答；尚未提供文件工具、命令执行或 Session 持久化。

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
