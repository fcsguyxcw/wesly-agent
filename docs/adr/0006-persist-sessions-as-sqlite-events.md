# Persist sessions as append-only SQLite events

Wesly will store task sessions in `%LOCALAPPDATA%\Wesly\wesly.db`, using append-only versioned events as the audit and recovery history plus mutable session projections for efficient queries. SQLite adds schema and migration work compared with per-session JSONL, but its transactions, locking, crash recovery, and query support provide the reliability required for resumable daily use without placing sensitive history inside user repositories.
