# Use a modular monolith

The personal Coding Agent will run as one Python process while separating the CLI, Agent loop, model adapters, tools, policy, context, and session storage into modules with controlled dependencies. This keeps local operation and debugging simple without collapsing responsibilities into one file; process isolation or remote services will be introduced only when an observed security, execution, or collaboration requirement justifies their operational cost.
