"""Outbound GitHub for the send_message tool — comment on an issue/PR thread.

Found live in the machine-events drill: session replies to GitHub mentions
had no sender at all ("unknown platform: github") — the relay adapter's
send() existed, but the send_message tool the mention framing points the
model at never learned GitHub. This is the sync, secrets-bound counterpart:
same auth ladder as the read tools (manual PAT wins; managed relay mints a
short-lived installation token — on a box, by machine credential), same
retry-once on a stale token.
"""

from __future__ import annotations

import os
from typing import Any

from ..secrets import SecretStore


def send_github_comment(secrets: SecretStore, chat_id: str, text: str) -> dict[str, Any]:
    """POST a comment on `owner/repo#N`. Returns the send_message result shape."""
    import httpx

    from .github_relay import split_thread
    from .integration_tools import _github_auth

    owner_repo, number = split_thread(chat_id)
    if number is None:
        return {"error": f"no issue/PR number in {chat_id!r} (expected owner/repo#N)"}
    owner = owner_repo.split("/")[0]
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    def _post(force: bool):
        headers, err = _github_auth(secrets, owner, force=force)
        if err:
            return None, err
        try:
            return (
                httpx.post(
                    f"{base}/repos/{owner_repo}/issues/{number}/comments",
                    json={"body": text},
                    headers=headers,
                    timeout=20,
                ),
                None,
            )
        except httpx.HTTPError as exc:
            return None, {"error": f"github unreachable: {type(exc).__name__}"}

    resp, err = _post(force=False)
    if resp is not None and resp.status_code == 401:
        resp, err = _post(force=True)  # stale cached token: re-mint once
    if err:
        return err
    if resp.status_code not in (200, 201):
        return {"error": f"github comment failed (http_{resp.status_code})"}
    body = resp.json()
    return {
        "ok": True,
        "message_id": str(body.get("id", "")),
        "target": f"github:{chat_id}",
    }
