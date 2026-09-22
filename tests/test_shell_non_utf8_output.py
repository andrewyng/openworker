"""A command whose output is not valid UTF-8 must not wedge the shell.

The executor reads the shell's stdout in text mode. Before the fix, one undecodable byte
(a binary dump, a Latin-1 log line) raised UnicodeDecodeError inside the reader thread,
the thread died, and the command hung until its timeout with no output. Seen on the
`make-mips-interpreter` task, where the agent cat'ed an ELF file.
"""

from __future__ import annotations

import sys
import time

import pytest

from coworker.tools.shell import LocalExecutor

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX shell: PowerShell re-encodes bytes before we see them"
)

# 0xE2 starts a three-byte UTF-8 sequence; followed by ASCII it is undecodable.
BAD_BYTES = r"printf 'before\xe2after\n'"


def test_undecodable_byte_does_not_hang_the_command(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, default_timeout=10)
    try:
        started = time.monotonic()
        result = ex.run(BAD_BYTES)
        assert time.monotonic() - started < 5, "command hung until the timeout"
        assert result["timed_out"] is False
        assert result["exit_code"] == 0
        assert "before" in result["output"] and "after" in result["output"]
        # The shell is still usable afterwards.
        assert ex.run("echo still_alive")["output"].strip() == "still_alive"
    finally:
        ex.close()


def test_undecodable_byte_in_background_output(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, default_timeout=10)
    try:
        task = ex.run_background(BAD_BYTES)
        deadline = time.monotonic() + 5
        output = ""
        while time.monotonic() < deadline:
            output += ex.background_output(task["task_id"]).get("output", "")
            if "after" in output:
                break
            time.sleep(0.1)
        assert "before" in output and "after" in output
    finally:
        ex.close()
