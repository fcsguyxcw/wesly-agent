# Use Python 3.12 for the Agent runtime

The Agent runtime will use Python 3.12 because the project prioritizes learning and rapidly iterating on the execution loop, tool system, context management, safety, and verification. Rust and TypeScript offer stronger distribution, startup, or type-system properties, but accepting Python's weaker single-binary packaging and lower performance keeps early architectural work visible and inexpensive; performance-sensitive components may be replaced only after measurement identifies a real bottleneck.
