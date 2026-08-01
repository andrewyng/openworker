import asyncio
from coworker.tools.subagent import explorer_tools

# We need a mock provider and engine to test it
class MockProvider:
    pass

tools = explorer_tools(workspace=".", provider=MockProvider(), model="gpt-4o")
delegate_tool = next(t for t in tools if t.name == "delegate")

print("Delegate Tool Found:", delegate_tool)
