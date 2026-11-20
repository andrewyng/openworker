"""One bounded MCP attachment. The environment process manager survives this client."""
import json
import os
import subprocess
import sys

TOOLS = {"process_start", "process_logs", "process_status", "process_stop"}


def call(request):
    operation = request["operation"]
    if request["workspace"] != os.environ["WORKSPACE_ID"] or operation["name"] not in TOOLS:
        raise ValueError()
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "workspace-provider", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": operation},
    ]
    proc = subprocess.run(
        ["/opt/openworker/openworker", "mcp-coding", "--workspace", "/data", "--environment-processes"],
        input="".join(json.dumps(m) + "\n" for m in messages), stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, timeout=20, check=True,
    )
    for line in proc.stdout.splitlines():
        response = json.loads(line)
        if response.get("id") == 2 and "result" in response:
            return response["result"]
    raise ValueError()


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.readline(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError()
        print(json.dumps({"result": call(json.loads(raw))}))
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        sys.exit(1)
