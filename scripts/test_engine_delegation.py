import asyncio
import os
from coworker.tools.subagent import explorer_tools

# We need a basic provider
import aisuite as ai
provider = ai.Client()

# Get tools
tools = explorer_tools(workspace=".", provider=provider, model="openai:gpt-4o-mini")

# Find delegate
delegate_func = None
for t in tools:
    if t.__name__ == "delegate":
        delegate_func = t

if delegate_func:
    print("Testing delegate...")
    # This will try to spawn a subagent using the "heavy" alias by default
    res = delegate_func(task="Run `echo 'hello'` using the shell.", allow_shell=True)
    print("Result:", res)
else:
    print("Delegate not found")

