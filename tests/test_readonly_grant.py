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
    "find . -newer build.log",
    "LC_ALL=C grep -c uses .github/workflows/ci.yml",
    "command -v semgrep",
    "wc -l file.txt | sort",
    "printf 'a: '",
    "awk '{print $1}' data.txt",
    "awk -F'|' '{print $1}' data.txt",
    "head -20 x | tail -5 | uniq -c",
    # -- the write/exec flags stay denied without taking the plain reads away ---------
    "sort data.txt",
    "xxd dump.bin",
    "xxd -p dump.bin",
    "file magic",
    "date -u +%Y",
    "du -sh .",
    "sed --expression='s/a/b/' data.txt",
    "sed -ne 's/a/b/' data.txt",
    "sed 's/wide/X/' wide.txt",
    "git grep -c needle",
    "git grep --cached needle",
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
    r"cat $env:APPDATA\coworker\secrets.json",  # PowerShell variable expansion
    r"cat $HOME/.config/coworker/secrets.json",  # POSIX variable expansion
    r"cat ${HOME}/.config/coworker/secrets.json",  # POSIX braced expansion
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
    "git grep -O./pwn.sh needle",             # exec via pager
    "git grep --open-files-in-pager=sh needle",
    "git grep --open-files-in-pager needle",  # pager defaults to less, still exec-shaped
    "/tmp/evil/cat file",                     # path-invoked binary
    "env FOO=1 rm x",
    "tee /tmp/x",
    "xargs rm",
    "ls | tee /tmp/x",                        # every pipeline stage must classify
    "ls |",                                   # dangling pipe
    "sudo cat /etc/shadow",
    # -- flags that turn a listed reader into a writer or an executor ---------------
    "sort -o /tmp/f data.txt",                # writes the sorted output
    "sort -o/tmp/f data.txt",                 # attached value
    "sort --output=/tmp/f data.txt",
    "sort --output /tmp/f data.txt",
    "sort -ro /tmp/f data.txt",               # cluster
    "sort --compress-program=./p data.txt",   # executes the compressor
    "xxd -r dump.hex out.bin",                # reverse mode writes the outfile
    "xxd payload.txt direct.bin",             # the outfile needs no flag at all
    "rg --pre ./pwn.sh pattern file.txt",     # executes the preprocessor
    "rg --pre=./pwn.sh pattern file.txt",
    "rg --hostname-bin=./pwn.sh pattern file.txt",
    "rg --pager ./pwn.sh pattern file.txt",
    "file -C -m magic",                       # compiles magic.mgc
    "file --compile -m magic",
    "date -s 2020-01-01",                     # sets the system clock
    "date --set=2020-01-01",
    "du --files0-from=/etc/list",             # reads filenames the scoper cannot see
    "sed -e'w /tmp/f' data.txt",              # attached -e script carries the write
    "sed --expression='w /tmp/f' data.txt",
    "sed -n '1wout.txt' data.txt",            # attached filename after an address
    "sed 's/a/b/w out.txt' data.txt",         # substitution flag
    "sed 's/a/b/wout.txt' data.txt",
    "sed '1w w1.txt' data.txt",
    "awk 'BEGIN{print \"x\" | \"sh\"}'",      # pipe to a command executes
    "awk 'BEGIN{\"id\" | getline x}'",
    "awk -f prog.awk data.txt",               # program file can carry system()/pipes
    "awk --file=prog.awk data.txt",
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
