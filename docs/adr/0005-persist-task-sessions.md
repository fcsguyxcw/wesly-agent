# Persist task sessions independently of the process

Each user task will have one durable session bound to its starting workspace, preserving messages, model responses, tool calls, permission decisions, and execution results across exits and crashes. Resuming requires checking for workspace drift, and only one execution loop may mutate a session at a time; this adds persistence and recovery complexity but is required for a dependable daily-use Agent rather than a terminal conversation that disappears with its process.
