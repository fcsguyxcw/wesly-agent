# Reviewable final answer

The live answer traced the read-only path as follows:

1. `run_cli` creates `ReadOnlyContextBuilder`, `ToolRegistry`, and `Agent`, then renders the Agent event stream. Evidence: [[src/wesly/cli.py]].
2. `Agent.run` sends chronological requests, executes model tool calls, appends tool results to history, accumulates `ToolResult.evidence_paths`, and rejects final citations absent from that observed set. Evidence: [[src/wesly/agent.py]].
3. `ToolRegistry` authorizes and executes `read_file`; successful reads return the workspace-relative file in `evidence_paths`. Evidence: [[src/wesly/tools.py]].
4. `extract_file_citations` extracts and normalizes final-answer paths for the Agent's evidence comparison. Evidence: [[src/wesly/evidence.py]].

The original live answer contained a longer line-by-line walkthrough and the same four evidence paths. No target file changed.
