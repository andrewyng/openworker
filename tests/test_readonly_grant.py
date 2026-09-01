"""The session-scoped read-only command grant (owner ask 2026-08-11).

The classifier is deliberately fail-closed: local reads and pure pipelines only. A false
negative costs one manual approval; a false positive costs an unreviewed side effect.
"""

from __future__ import annotations

import pytest

from coworker.permissions import Mode, PermissionEngine
from coworker.readonly import is_readonly_command

ACCEPT = [
    "ls -la",
    "cat README.md",
    "grep -rn 'pattern' src",
    "rg --json TODO",
    "nl -ba tests/test_x.py | sed -n '320,345p'",
    "nl -ba a.py | sed -E \"s/'[^']*'/'[REDACTED]'/g\"",
    "git log --oneline -5",
    "git diff main...HEAD",
    "git status",
    "git -C /tmp/repo log -1",
    "git branch --show-current",
    "git stash list",
    "git config --get user.name",
    "git remote -v",
    "jq '.results | length' /tmp/report.json",
    "find . -name '*.py'",
    "LC_ALL=C grep -c uses .github/workflows/ci.yml",
    "command -v semgrep",
    "wc -l file.txt | sort",
    "printf 'a: '",
    "awk '{print $1}' data.txt",
    "head -20 x | tail -5 | uniq -c",
]

REJECT = [
    "",
    "rm -rf /",
    "cat a > b",                              # redirection
    "cat a >> b",
    "grep x f 2>/dev/null",                   # even stderr redirects
    "ls; rm -rf /",                           # chaining
    "ls && touch x",
    "cat `whoami`",                           # substitution
    "cat $(secret)",
    "grep '$(x)' file",                       # can't tell quoted-safe apart — fail closed
    "curl https://api.github.com/repos/x",    # network = exfil channel, excluded
    "wget http://x",
    "ssh host ls",
    "python3 -c 'print(1)'",                  # interpreters
    "bash -c ls",
    "sed -i 's/a/b/' f",                      # in-place write
    "sed -n 'w /tmp/x' f",                    # sed write command
    "sed -f script.sed f",                    # script file could carry w
    "awk '{print > \"f\"}' x",                # awk redirection
    "awk 'BEGIN{system(\"rm x\")}'",
    "find . -delete",
    "find . -exec rm {} ;",
    "git push origin main",
    "git branch new-branch",                  # creates
    "git tag v1",                             # creates
    "git stash",                              # writes
    "git config user.name evil",              # writes
    "git -c core.pager='touch x' log",        # exec hook via -c
    "git log --output=/tmp/f",                # write via flag
    "/tmp/evil/cat file",                     # path-invoked binary
    "env FOO=1 rm x",
    "tee /tmp/x",
    "xargs rm",
    "ls | tee /tmp/x",                        # every pipeline stage must classify
    "ls |",                                   # dangling pipe
    "sudo cat /etc/shadow",
]


@pytest.mark.parametrize("cmd", ACCEPT)
def test_classifier_accepts(cmd):
    assert is_readonly_command(cmd) is True, cmd


@pytest.mark.parametrize("cmd", REJECT)
def test_classifier_rejects(cmd):
    assert is_readonly_command(cmd) is False, cmd


def test_engine_grant_gates_on_classifier(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)

    class Meta:
        category = "shell"
        risk_level = "high"
        capabilities = ["exec"]

    # Before the grant: a read-only command still asks.
    d = eng.evaluate("run_shell", {"command": "ls -la"}, Meta())
    assert d.needs_user

    eng.allow_readonly_for_session()
    assert eng.evaluate("run_shell", {"command": "ls -la"}, Meta()).allowed
    assert eng.evaluate("run_shell", {"command": "git log -1"}, Meta()).allowed
    # The grant never covers writes/network — those keep asking.
    assert eng.evaluate("run_shell", {"command": "rm -rf x"}, Meta()).needs_user
    assert eng.evaluate("run_shell", {"command": "curl https://x"}, Meta()).needs_user


def test_grant_persists_via_session_grants(tmp_path):
    from coworker.server.manager import _grants_of

    class FakeEngine:
        class permissions:
            session_allow_tools = set()
            session_allow_commands = set()
            session_readonly = True

    grants = _grants_of(FakeEngine)
    assert grants == {"tools": [], "commands": [], "readonly": True}
