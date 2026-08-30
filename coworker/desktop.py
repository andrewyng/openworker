"""The user's graphical session, as seen from a process that did not inherit it.

This install's server is started by systemd at BOOT — before anyone logs in — so its own
environment has no DISPLAY, and it never gains one for the rest of the uptime. Anything it
launches that needs a screen (the folder picker, headed Chromium) therefore dies with
"cannot open display", while the same code run from a terminal works perfectly.

The systemd USER manager does gain DISPLAY/XAUTHORITY at login (`systemctl --user
import-environment`), so it can be asked. It must be asked at CALL time: at import or startup
the answer is still "no session", and caching that is what makes the failure survive until the
next restart. Restarting the service after login also fixes it — until the next reboot puts
the ordering back.
"""

from __future__ import annotations

import os
import subprocess

# The variables a GUI subprocess needs. XAUTHORITY alone is not enough and DISPLAY alone
# usually is, but both are carried so an X server with a per-session cookie also works.
_VARS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")


def desktop_env() -> dict[str, str]:
    """`os.environ` plus the display variables, recovered from the systemd user manager when
    this process did not inherit them. Never raises: no session simply means no addition."""
    env = dict(os.environ)
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return env
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return env
    for line in (out.stdout or "").splitlines():
        key, _, value = line.partition("=")
        if key in _VARS and value:
            env[key] = value
    return env


def has_display(env: dict[str, str] | None = None) -> bool:
    """Is a screen reachable at all? Ask before promising a headed launch will work."""
    env = desktop_env() if env is None else env
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
