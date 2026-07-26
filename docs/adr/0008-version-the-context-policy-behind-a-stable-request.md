# Version context policies behind a stable model request

Wesly will keep a stable internal `ModelRequest` boundary while versioned context policies decide which instructions, messages, tools, and bounded tool pages enter each request. The first `chronological-v1` policy preserves current-session order, refuses silent compression, and blocks at a 56K input budget with 8K reserved output; this exposes real pressure now while allowing later summary or retrieval policies without coupling the Agent loop or provider adapter to one selection algorithm.
