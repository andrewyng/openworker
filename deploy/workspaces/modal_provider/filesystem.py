"""Workspace file operations on a Modal sandbox with one v2 Volume at /data."""
import json
from pathlib import Path

import modal


def runner_image():
    helper = Path(__file__).resolve().parents[1] / "files.py"
    return modal.Image.debian_slim(python_version="3.12").add_local_file(helper, "/opt/openworker/files.py")


def file_operation(sandbox, workspace_id, operation):
    """Caller serializes operations for this sandbox while its mount is reloaded."""
    payload = json.dumps({"workspace": workspace_id, "operation": operation}) + "\n"
    if len(payload.encode()) > 1_048_576:
        raise ValueError("Workspace request too large")
    sandbox.reload_volumes(timeout=20)
    process = sandbox.exec("python", "/opt/openworker/files.py", timeout=15)
    process.stdin.write(payload)
    process.stdin.write_eof()
    process.stdin.drain()
    reply = process.stdout.read()
    if process.wait() != 0 or len(reply.encode()) > 1_048_576:
        raise RuntimeError("Workspace file helper did not complete")
    result = json.loads(reply)
    if "result" not in result:
        raise ValueError("Invalid workspace file operation")
    if operation["name"] in {"write_file", "edit_file"}:
        # Volumes v2 supports explicit commit via sync on the mountpoint.
        # Once a mutation has run, an uncertain commit must not replay it.
        if sandbox.exec("sync", "/data", timeout=20).wait() != 0:
            raise RuntimeError("Workspace volume commit could not be confirmed")
    return result["result"]
