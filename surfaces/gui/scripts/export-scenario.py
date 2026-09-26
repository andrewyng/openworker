#!/usr/bin/env python3
"""Save one real session as a card-gallery scenario (dev tooling; stdlib only).

  export-scenario.py <session_id> --id lead-issue-3 --title "SWE Lead · issue #3"
      [--base http://127.0.0.1:8765/v1] [--token-file PATH] [--note TEXT]
      [--out src/gallery/scenarios] [--max-tool-chars 1200] [--replace OLD=NEW ...]

Reads the session row, its workers, the stored transcript, the pending Inbox items and
the board from a running server (`--base` is the API root up to and including /v1; for a
session on a joined machine, pass that machine's proxy root). The token, when one is
needed, is read from a FILE so it never appears in argv or shell history.

What is written is scrubbed: secrets and bearer tokens, email addresses, home
directories, AWS account ids, and long tool output (truncated). The session id is
replaced by a scenario-local one. `scenarios.test.ts` re-checks every file, so a leak
fails the unit tests too. Exports are for THIS machine: name them `real-…` and they are
gitignored (`src/gallery/scenarios/real-*.json`). Scrubbing catches patterns, not meaning,
so a real session is never committed; write a hand-made scenario for anything shared.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "sk-REDACTED"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "gh_REDACTED"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "gh_REDACTED"),
    (re.compile(r"xox[abpr]-[A-Za-z0-9-]{10,}"), "xox-REDACTED"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIAREDACTED"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{20,}"), r"\1REDACTED"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT_REDACTED"),
    (re.compile(r"(?i)((?:password|passwd|secret|api[_-]?key|token)\"?\s*[:=]\s*\"?)[^\s\"',}]{8,}"), r"\1REDACTED"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "PRIVATE_KEY_REDACTED"),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HOME = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+")
AWS_ACCOUNT = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")


def scrub_text(text: str) -> str:
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    text = EMAIL.sub("user@example.com", text)
    text = HOME.sub("/home/user", text)
    return AWS_ACCOUNT.sub("123456789012", text)


def scrub(value):
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_id")
    ap.add_argument("--id", required=True, help="scenario id (file name and URL slug)")
    ap.add_argument("--title", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--base", default="http://127.0.0.1:8765/v1")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "src", "gallery", "scenarios"))
    ap.add_argument("--max-tool-chars", type=int, default=1200)
    ap.add_argument("--replace", action="append", default=[], metavar="OLD=NEW",
                    help="replace a name everywhere (a GitHub handle, a customer); repeatable")
    args = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", args.id):
        ap.error("--id must be lowercase letters, digits and dashes")
    if not args.id.startswith("real-"):
        args.id = "real-" + args.id  # the gitignored prefix: an export never gets committed

    headers = {}
    if args.token_file:
        with open(os.path.expanduser(args.token_file)) as fh:
            headers["Authorization"] = "Bearer " + fh.read().rstrip()

    def get(path: str, default=None):
        try:
            req = urllib.request.Request(args.base.rstrip("/") + path, headers=headers)
            return json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as exc:  # noqa: BLE001 - optional endpoints may be missing on older servers
            if default is None:
                raise SystemExit(f"GET {path} failed: {exc}")
            return default

    sid = args.session_id
    quoted = urllib.parse.quote(sid)
    rows = get("/sessions")
    rows = rows if isinstance(rows, list) else rows.get("sessions", [])
    row = next((s for s in rows if s.get("session_id") == sid), None)
    if row is None:
        raise SystemExit(f"no session {sid} on {args.base}")
    workers = [s for s in rows if (s.get("team") or {}).get("lead_session") == sid]
    messages = get(f"/sessions/{quoted}/messages").get("messages", [])
    pending = get(f"/inbox?state=pending&session_id={quoted}", {"items": []}).get("items", [])
    board = get(f"/sessions/{quoted}/board", {})
    unattended = get(f"/sessions/{quoted}/unattended", {}).get("unattended")

    for m in messages:  # tool output is the bulk of a transcript and the likeliest leak
        if m.get("role") == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > args.max_tool_chars:
            cut = len(m["content"]) - args.max_tool_chars
            m["content"] = m["content"][: args.max_tool_chars] + f"\n… [{cut} characters cut by export-scenario]"

    local_sid = "scn-" + args.id
    team = row.get("team") or {}
    scenario = {
        "id": args.id,
        "title": args.title,
        "note": args.note,
        "exported": {"from_session": sid[:8] + "…", "messages": len(messages)},
        "session": {
            "session_id": local_sid,
            "title": row.get("title") or "",
            "agent": row.get("agent") or "",
            "model": row.get("model") or "",
            "mode": row.get("mode") or "interactive",
            "workspace": row.get("workspace") or "",
            # No answer (older server): a session with parked items is, in effect, unattended.
            "unattended": bool(unattended) if unattended is not None else bool(pending),
        },
        "messages": messages,
        "pending": pending,
    }
    if workers:
        scenario["team"] = {
            "team_id": team.get("team_id") or "team",
            "chat_enabled": bool(team.get("chat_enabled")),
            "workers": [
                {
                    "name": (w.get("team") or {}).get("actor") or w.get("title") or "worker",
                    "persona": w.get("agent") or "",
                    "model": w.get("model") or "",
                    "status": (w.get("team") or {}).get("status") or "idle",
                    "current_item": (w.get("team") or {}).get("current_item") or "",
                    "usage": w.get("usage") or {},
                }
                for w in workers
            ],
        }
    if isinstance(board, dict) and board.get("items"):
        scenario["board"] = board

    # The real session id appears inside items, board rows and sometimes tool output.
    text = json.dumps(scrub(scenario), indent=2, ensure_ascii=False).replace(sid, local_sid)
    for pair in args.replace:
        old, _, new = pair.partition("=")
        if old:
            text = text.replace(old, new)
    out = os.path.join(args.out, args.id + ".json")
    with open(out, "w") as fh:
        fh.write(text + "\n")
    print(f"wrote {os.path.normpath(out)}: {len(messages)} messages, {len(pending)} pending, {len(workers)} workers")
    print("Gitignored (real-*.json): it stays on this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
