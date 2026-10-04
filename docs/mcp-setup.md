# Adding MCP Servers to OpenWorker

OpenWorker supports integrating with Model Context Protocol (MCP) servers via configuration files. This allows you to extend the agent's capabilities with tools from any standard-compliant MCP server.

## Configuration Files
OpenWorker looks for `mcp.json` in two locations:
1.  **Workspace Specific:** `<workspace>/.coworker/mcp.json` (overrides global settings).
2.  **Global:** `~/.config/coworker/mcp.json`

## Configuration Format
The configuration follows the standard MCP configuration format:

```json
{
  "mcpServers": {
    "server-name": {
      "command": "node",
      "args": ["/path/to/server/index.js"],
      "env": {
        "API_KEY": "your_key"
      }
    }
  }
}
```

## Key Features
- **Auto-detection:** The system automatically identifies the transport layer (`stdio` or `HTTP` / `SSE`) based on your configuration.
- **Variable Expansion:** Use `${VAR}` syntax in `command`, `args`, `env`, `url`, and `headers`. These are resolved at load time using the system's environment and `.env` files.
- **Tool Filtering:** Optionally restrict which tools from an MCP server are exposed to the agent:
  - `include_tools`: Explicit list of allowed tool names.
  - `exclude_tools`: List of blocked tool names.

## Quick Start Example
To add a local server, create `.coworker/mcp.json` in your project root:
```json
{
  "mcpServers": {
    "my-tool": {
      "command": "python",
      "args": ["/path/to/server.py"],
      "env": {
        "DEBUG": "true"
      }
    }
  }
}
```

The agent will recognize these tools immediately on the next run.
