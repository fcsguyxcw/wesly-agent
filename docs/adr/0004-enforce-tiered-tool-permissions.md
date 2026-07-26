---
status: superseded by ADR-0007
---

# Enforce tiered permissions at the tool boundary

Every tool call will pass through one permission policy before execution: routine reads, workspace edits, builds, searches, and tests may run automatically, while writes outside the workspace, credential access, software installation, GUI launch, privilege escalation, destructive filesystem operations, and dangerous Git operations require approval. Structured audit events will capture every decision; this adds policy machinery but preserves useful autonomy without treating prompt instructions as a security boundary.
