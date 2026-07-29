"""Shared helpers for local, user-scoped state directories and files.

Extracted so secret storage and durable tool-output storage share one
cross-platform ACL path without importing each other.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"


def restrict_to_user(path: Path, *, is_dir: bool) -> None:
    """Restrict a path so only the current user can access it.

    POSIX expresses this with mode bits (0700 dir / 0600 file). Windows has no such bits —
    `os.chmod` there only toggles the read-only flag, so a 0600 chmod is a silent no-op and
    the file inherits broad ACLs (SYSTEM, Administrators, …). Use an ACL instead: strip
    inherited entries and grant the current user alone. Best-effort on Windows so a transient
    icacls failure never blocks saving state.
    """
    if _IS_WINDOWS:
        user = os.environ.get("USERNAME")
        if not user:
            return
        domain = os.environ.get("USERDOMAIN")
        account = f"{domain}\\{user}" if domain else user
        # A directory grant MUST be inheritable — (OI) object-inherit for files, (CI)
        # container-inherit for subdirs — so everything created inside inherits the
        # user's access. Without these flags, /inheritance:r leaves the directory with
        # a non-inheritable ACE and any child file ends up with an empty DACL.
        grant = f"{account}:(OI)(CI)F" if is_dir else f"{account}:F"
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
                capture_output=True,
                check=False,
            )
        except OSError:
            pass
        return
    os.chmod(path, 0o700 if is_dir else 0o600)
