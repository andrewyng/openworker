import asyncio
import os
from coworker.tools.subagent import explorer_tools

# We need a basic provider
import aisuite as ai
provider = ai.Client()

# Get tools
tools = explorer_tools(workspace=".", provider=provider, model="openai:gpt-4o")

# Find delegate
delegate_func = None
for t in tools:
    if getattr(t, "__name__", "") == "delegate":
        delegate_func = t

if delegate_func:
    print("Spawning subagent to test Knowledge Pack Injection...")
    res = delegate_func(task="What activation function should I use in the output layer of the generator when building a GAN? Check my knowledge packs.", target_model="heavy")
    print("\n--- Final Report from Subagent ---")
    print(res)
else:
    print("Delegate tool not found")
