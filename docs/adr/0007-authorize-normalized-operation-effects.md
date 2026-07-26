---
status: accepted
---

# Authorize normalized operation effects, not tool names

Every tool request will be converted into an immutable normalized operation and authorized from its resolved target, effects, sensitivity, and verifiable preconditions; execution revalidates those conditions, approvals bind one exact operation, and policy or audit failures deny execution. This supersedes ADR-0004's automatic allowance for routine build and test commands: without an OS sandbox those commands can execute arbitrary code, so all shell commands require per-operation approval until a separately proven isolation or trust mechanism exists.
