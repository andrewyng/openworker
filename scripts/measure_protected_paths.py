"""Replay recorded agent runs and count the calls that newly need a person because of the
"files that run later" floor (OPE-231), compared with an earlier revision.

Reads the runs only. Two formats are understood:

* OpenWorker transcripts: `messages.json` files (OpenAI-style tool calls), given as a glob;
* SWE-smith rows: a JSON file with `{"rows": [{"row": {"messages": ...}}]}`, whose tools
  are `str_replace_editor` and `bash`.

    python scripts/measure_protected_paths.py --before origin/main \
        --openworker "runs/**/messages.json" --swe-smith rows.json

Shell commands are read as bash (both sources ran on Linux). A file-tool write counts when
the earlier revision did not protect its path and this one does; a shell command counts
when this revision asks a person for it (the earlier one had no shell check).
"""

from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from coworker import permissions as now  # noqa: E402

WRITE_TOOLS = ("write_file", "replace_in_file", "apply_patch", "apply_unified_diff")
EDITOR_WRITES = ("create", "str_replace", "insert", "undo_edit")
SHELL_TOOLS = ("run_shell", "bash", "execute_bash")


def load_before(rev: str):
    src = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{rev}:coworker/permissions.py"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    path = Path(tempfile.mkdtemp()) / "permissions_before.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("coworker.permissions_before", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def marker_of(text: str, markers) -> str | None:
    for m in markers:
        if now._names_protected(text, (m,)):
            return m
    return None


def tool_calls(msgs):
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        for c in m.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except ValueError:
                    args = {}
            yield fn.get("name", ""), args if isinstance(args, dict) else {}


def write_targets(name: str, args: dict) -> list[str]:
    if name in WRITE_TOOLS:
        return now.write_paths(name, args)[0]
    if name == "str_replace_editor" and args.get("command") in EDITOR_WRITES:
        return [str(args.get("path", ""))]
    return []


class Tally:
    def __init__(self, label: str, before, engine):
        self.label, self.before, self.engine = label, before, engine
        self.runs = self.writes = self.shells = 0
        self.hits = collections.Counter()
        self.hit_runs = collections.defaultdict(set)
        self.examples = collections.defaultdict(list)
        self.reads = collections.Counter()

    def hit(self, run: str, key: tuple[str, str], example: str) -> None:
        self.hits[key] += 1
        self.hit_runs[key[0]].add(run)
        if len(self.examples[key]) < 3:
            self.examples[key].append(example[:160])

    def replay(self, run: str, msgs) -> None:
        self.runs += 1
        for name, args in tool_calls(msgs):
            for p in filter(None, write_targets(name, args)):
                self.writes += 1
                cand = Path(p)
                if now._is_protected_in_project(cand) and not self.before._is_protected_in_project(cand):
                    marker = marker_of(p, now._PROTECTED_IN_PROJECT) or "(where it lands on disk)"
                    self.hit(run, ("file tool", marker), f"{name}: {p}")
            if name not in SHELL_TOOLS:
                continue
            cmd = str(args.get("command", "") or "")
            if not cmd.strip():
                continue
            self.shells += 1
            text = cmd.replace("\\", "/")
            words = [w for w in now._WORD_BREAK.split(re.sub(r"[\"'`]", "", text)) if w]
            if self.engine._shell_changes_protected(cmd):
                argvs = []
                for part in now._split_commands(text):
                    try:
                        argvs.append(shlex.split(part))
                    except ValueError:
                        argvs.append(part.split())
                if any(now._changes_git_config(a) for a in argvs):
                    marker = "git config (a setting that is not a plain one)"
                else:
                    found = [marker_of(w, now._shell_markers()) for w in words if not w.startswith("-")]
                    marker = next((m for m in found if m), "(where it lands on disk)")
                self.hit(run, ("shell", marker), cmd)
            elif self.engine._only_reads(cmd):
                found = [marker_of(w, now._PROTECTED_IN_PROJECT) for w in words]
                if any(found):
                    self.reads[next(m for m in found if m)] += 1

    def report(self) -> None:
        print(f"\n== {self.label}: {self.runs} runs, {self.writes} file-tool writes, "
              f"{self.shells} shell commands")
        if not self.hits:
            print("   nothing newly needs a person")
        for channel in ("file tool", "shell"):
            keys = [k for k in self.hits if k[0] == channel]
            if not keys:
                continue
            total = sum(self.hits[k] for k in keys)
            print(f"   {channel}: {total} calls in {len(self.hit_runs[channel])} runs")
            for key in sorted(keys, key=lambda k: -self.hits[k]):
                print(f"      {self.hits[key]:5d}  {key[1]}")
                for ex in self.examples[key]:
                    print(f"             e.g. {ex!r}")
        if self.reads:
            print(f"   reads naming a protected path, still handled as reads: {sum(self.reads.values())}")
            for marker, n in self.reads.most_common(6):
                print(f"      {n:5d}  {marker}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--before", default="origin/main", help="revision to compare with")
    ap.add_argument("--openworker", help="glob of OpenWorker messages.json files")
    ap.add_argument("--swe-smith", help="SWE-smith rows JSON file")
    a = ap.parse_args()
    before = load_before(a.before)
    engine = now.PermissionEngine(
        workspace_root=Path(tempfile.mkdtemp()), mode=now.Mode.AUTO_APPROVE, shell_kind="bash"
    )
    print(f"compared with {a.before}")
    if a.swe_smith:
        t = Tally("SWE-smith", before, engine)
        data = json.load(open(a.swe_smith, encoding="utf-8"))
        for i, row in enumerate(data["rows"]):
            r = row.get("row", row)
            msgs = json.loads(r["messages"]) if isinstance(r["messages"], str) else r["messages"]
            t.replay(f"swe-{i}", msgs)
        t.report()
    if a.openworker:
        t = Tally("OpenWorker", before, engine)
        for p in sorted(glob.glob(a.openworker, recursive=True)):
            try:
                msgs = json.load(open(p, encoding="utf-8"))
            except ValueError:
                continue
            t.replay(p, msgs)
        t.report()


if __name__ == "__main__":
    main()
