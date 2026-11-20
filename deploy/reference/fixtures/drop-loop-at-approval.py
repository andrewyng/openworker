#!/usr/bin/python3
"""Test image entry point. Break the exec stream after durable approvals are pending."""
import os
import signal
import subprocess
import sys

runner = "/opt/openworker/openworker.real"
if sys.argv[1:] != ["sandbox-loop"]:
    os.execv(runner, [runner, *sys.argv[1:]])

child = subprocess.Popen([runner, "sandbox-loop"], stdin=subprocess.PIPE)
for frame in sys.stdin.buffer:
    if frame.startswith(b'{"park":'):
        child.kill()
        child.wait()
        os.kill(os.getpid(), signal.SIGKILL)
    child.stdin.write(frame)
    child.stdin.flush()
child.stdin.close()
sys.exit(child.wait())
