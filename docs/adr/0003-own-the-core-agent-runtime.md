# Own the core Agent runtime

The project will implement its Agent loop, tool protocol, context management, and permission control directly instead of adopting an Agent framework such as LangChain, LangGraph, or AutoGen. General-purpose libraries remain allowed for HTTP, validation, CLI behavior, and testing; accepting more protocol and orchestration work keeps the system's essential decisions visible, testable, and under our control rather than hidden behind framework conventions.
