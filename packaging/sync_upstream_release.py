#!/usr/bin/env python3
"""Open a reviewable integration PR; never merge or publish a release.

Run only in a disposable, clean checkout of the fork. GH_TOKEN is provided by CI.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

FORK = "Pol-Lanski/openworker"
UPSTREAM = "andrewyng/openworker"
BASE = "codex/nexus-provider"


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def release_tag(release: dict) -> str:
    tag = release.get("tag_name", "")
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Expected a published stable vMAJOR.MINOR.PATCH release")
    return tag


def main() -> None:
    if run("git", "status", "--porcelain"):
        raise RuntimeError("Use a clean disposable checkout; local edits must be preserved")
    remote = run("git", "remote", "get-url", "origin")
    if remote not in (f"https://github.com/{FORK}.git", f"https://github.com/{FORK}", f"git@github.com:{FORK}.git"):
        raise RuntimeError("Refusing to write to a repository other than the Nexus fork")
    release = json.loads(run("gh", "api", f"repos/{UPSTREAM}/releases/latest"))
    tag = release_tag(release)
    run("git", "fetch", "origin", BASE)
    base_sha = run("git", "rev-parse", "FETCH_HEAD")
    run("git", "fetch", "--no-tags", f"https://github.com/{UPSTREAM}.git", f"refs/tags/{tag}")
    sha = run("git", "rev-parse", "FETCH_HEAD^{commit}")
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, base_sha]).returncode == 0:
        print(f"{tag} ({sha}) is already integrated; nothing to do")
        return
    branch = f"codex/upstream-{tag}-{sha[:12]}"
    existing = run("git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if existing:
        run("git", "fetch", "origin", branch)
        run("git", "checkout", "-b", branch, "FETCH_HEAD")
    else:
        run("git", "checkout", "-b", branch, base_sha)
    try:
        run("git", "merge", "--no-edit", base_sha)
        run("git", "merge", "--no-edit", sha)
    except subprocess.CalledProcessError:
        subprocess.run(["git", "merge", "--abort"], check=False)
        raise RuntimeError(f"Merge conflict integrating {tag} ({sha}); no branch was pushed. Resolve manually.") from None
    # Verify the result before pushing; a clean git merge alone is insufficient.
    run("python3", "packaging/check_fork_policy.py")
    marker = Path(".github/nexus-upstream.json")
    marker.write_text(json.dumps({"repository": UPSTREAM, "tag": tag, "sha": sha}, indent=2) + "\n")
    run("git", "add", str(marker))
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
        run("git", "commit", "-m", f"Record upstream {tag} at {sha}")
    run("git", "push", "origin", f"HEAD:refs/heads/{branch}")
    prs = json.loads(run("gh", "pr", "list", "--repo", FORK, "--base", BASE, "--head", branch, "--state", "open", "--json", "number"))
    body = (f"Integrates upstream stable release [{tag}](https://github.com/{UPSTREAM}/releases/tag/{tag}) "
            f"at commit `{sha}` into `{BASE}`, preserving the fork's Nexus provider.\n\n"
            "Review the diff and require CI (backend, GUI unit/typecheck, GUI e2e, fork policy) before merging. "
            "Conflicts are never auto-resolved. This PR does not publish a release. "
            "After merge, dispatch Release from the reviewed commit for installer artifacts. "
            "A signed updater release additionally requires the fork signing setup and a version bump.\n")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as f:
        f.write(body)
        f.flush()
        if prs:
            run("gh", "pr", "edit", str(prs[0]["number"]), "--repo", FORK, "--body-file", f.name)
        else:
            run("gh", "pr", "create", "--repo", FORK, "--base", BASE, "--head", branch,
                "--title", f"Integrate upstream {tag} into Nexus fork", "--body-file", f.name)
    # GITHUB_TOKEN pushes do not reliably start ordinary push/PR checks. Dispatch
    # explicitly against this branch so checks attach to the candidate commit.
    run("gh", "workflow", "run", "ci.yml", "--repo", FORK, "--ref", branch)
    print(f"Integration PR ready for {tag} ({sha}); CI dispatched on {branch}")


if __name__ == "__main__":
    main()
