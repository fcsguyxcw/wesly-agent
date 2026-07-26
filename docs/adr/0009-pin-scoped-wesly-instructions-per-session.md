# Pin scoped WESLY.md instructions per Session

Wesly v1 will automatically load only global and workspace `WESLY.md` files, applying more specific directory scopes over ancestor and global guidance while keeping built-in safety and explicit user requests higher. A bounded, hashed snapshot of every applicable instruction file is fixed when the Session is created and reused on resume; this sacrifices hot reload and existing `AGENTS.md` compatibility in exchange for reproducible context, explicit ownership, and protection against an Agent changing instructions to influence its current run.
