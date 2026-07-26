# PROTOTYPE — Wesly CLI interaction

This throwaway prototype answers one question: which task state, model activity, tool activity, approval, interruption, failure, and final-result information must remain visible in Wesly's minimum CLI interaction contract?

It does not call a model, execute tools, or persist sessions. Drive the state machine manually and judge whether each transition exposes enough information without showing provider internals or chain-of-thought.

## Verdict

The production CLI will use a scrollable activity log rather than this prototype's full-screen state dashboard. The debug state and manual actions are prototype-only; the validated contract is concise activity, explicit approvals, honest completion/failure output, file evidence, and optional safe diagnostics through `--verbose`.

Run from the repository root:

```powershell
& 'C:\Users\a1324\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' '.\.scratch\wesly-foundation\prototypes\cli-interaction\tui.py'
```
