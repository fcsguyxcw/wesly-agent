# Read-only understanding evaluations

Ticket 15 freezes two repository-understanding tasks. These are manual live evaluations, not part of the default test suite. The task JSON is immutable once a recorded run references its `id` and `version`.

## Run contract

1. Create a fresh directory outside the source checkout and clone the task's `repository.url`.
2. Check out the exact `repository.commit` in detached-HEAD state and confirm `git status --porcelain=v1` is empty.
3. From the Wesly source checkout, run the committed `.venv\Scripts\wesly.exe --verbose "<prompt>"` with the evaluation repository as the current directory.
4. Save stdout and stderr separately under `evals/runs/<run-id>/`; never save `DEEPSEEK_API_KEY` or the process environment.
5. Confirm the target repository HEAD is unchanged and `git status --porcelain=v1` is still empty.
6. Copy `evals/result-template.json` to the run directory, fill the task/model/usage/evidence fields, and assign exactly one status: `pass`, `fail`, `blocked`, or `invalid`.
7. Mark `pass` only when every success criterion and forbidden-change check in the task JSON is satisfied. A provider/network failure is `blocked`; a wrong checkout or changed task contract is `invalid`.

Runs are append-only evidence. A retry uses a new run ID and does not overwrite an earlier result.
