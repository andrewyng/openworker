"""OPE-232: the read-only checker allows only the flags known to be harmless.

Every command the checker accepts has a list of the flags that only change what is
shown or which local files are read. Any other flag, including one nobody has reviewed
yet, makes the command not read-only, so it gets a card. The same holds for git
subcommands and for the PowerShell cmdlets the checker knows.

The small languages inside sed, awk and jq are accepted only in a conservative subset,
and every path a flag carries is a read target that the session's folders are checked
against.
"""

from __future__ import annotations

import pytest

from coworker.readonly import is_readonly_command, read_targets


def ok(command: str, shell: str = "bash") -> bool:
    return is_readonly_command(command, shell=shell)


# -- the flags agents really use keep passing -----------------------------------------------

ACCEPT = [
    # ls / cat / head / tail / wc
    "ls -la", "ls -lt src", "ls -ld src", "ls -R", "ls -1", "ls -lah --color=auto",
    "ls --time-style=+%s -l", "ls --time-style full-iso -l", "ls -S", "ls --sort=size",
    "cat -A x.c", "cat -n x.c", "cat -v x.c", "cat -An x.c",
    "head -20 x", "head -n 5 x", "head -n5 x", "head -c 100 x", "head -c 1K x", "head -n -5 x",
    "head --lines=5 x", "tail -3 x", "tail -n +5 x", "tail -c 64 x", "tail -n1 x",
    "wc -l x", "wc -c x", "wc -lw x", "wc --lines x",
    # grep and friends
    "grep -n foo x", "grep -rn foo src", "grep -v foo x", "grep -c foo x", "grep -h foo x",
    "grep -i foo x", "grep --include=*.py -rn foo src", "grep -A 5 foo x", "grep -A25 foo x",
    "grep -B2 foo x", "grep -C 3 foo x", "grep -E 'a|b' x", "grep -m1 foo x", "grep -l foo x",
    "grep -rln foo src", "grep -a foo x", "grep -o foo x", "grep -oE 'a+' x", "grep -aobE x y",
    "grep -e foo -e bar x", "grep -w foo x", "grep -x foo x", "grep -F 'a.b' x", "grep -5 foo x",
    "grep --color=never foo x", "grep --exclude-dir=.git -rn foo .", "grep -q foo x",
    "egrep 'a|b' x", "fgrep a.b x", "grep -rI foo src", "grep -L foo src/x",
    "rg --json TODO", "rg -n foo src", "rg -i foo", "rg --files src", "rg -g '*.py' foo",
    "rg -t py foo", "rg -C 2 foo", "rg -l foo", "rg --hidden foo", "rg -uu foo",
    "ugrep -n foo x",
    # text tools
    "sort -rn x", "sort -u x", "sort -n x", "sort -k2 x", "sort -k6,7 x", "sort -t- -k2 x",
    "sort -t , -k 1,1 x", "uniq -c", "uniq -d x", "cut -c60- x", "cut -d, -f1 x",
    "cut -f 1 x", "cut -c1-200 x", "tr -d '\\r'", "tr a-z A-Z", "nl -ba x", "nl x",
    "column -t x", "comm -12 a b", "diff -u a b", "diff -q a b", "diff --label a -u a b",
    "diff -U3 a b", "diff -rq --no-dereference a b", "strings -n 8 x", "strings x",
    "od -A x -t x1z -v x", "od -c x", "od -N 16 -j 4 x", "hexdump -C x", "xxd x",
    "xxd -p x", "xxd -l 64 x", "xxd -s 0x100 -l 64 x", "xxd -c16 x",
    "file x", "file -b x", "file --mime-type x", "stat x", "stat -c %s x",
    "du -sh .", "du -h -d 1 .", "df -h", "df -h .",
    "md5sum x", "sha256sum x", "shasum -a 256 x", "sha1sum x",
    # small utilities
    "pwd", "whoami", "id -u", "date", "date -u +%Y", "date -d @0 +%s", "uname -a",
    "basename a/b.c .c", "dirname a/b.c", "realpath x", "readlink -f x", "which -a git",
    "echo hello", "echo -n hi", "printf 'a: '", "true", "false",
    "command -v semgrep", "command -V git",
    # git
    "git status", "git status --short", "git status -sb", "git status --porcelain",
    "git log --oneline -5", "git log --oneline", "git log -1", "git log -n 3",
    "git log --all --graph --oneline", "git log --format=%H -1", "git log --pretty=format:%s -3",
    "git log --grep=fix -i", "git log --since=2.weeks --until=yesterday",
    "git log --follow -- src/x.py", "git log -p -1", "git log --stat -3", "git log --find-object=abc",
    "git log --date=short --format='%ad %s'", "git log --author=me", "git log main..HEAD",
    "git diff", "git diff --stat", "git diff --cached", "git diff --name-only HEAD~1",
    "git diff main...HEAD", "git diff -U0 a b", "git diff --src-prefix=a/ --dst-prefix=b/",
    "git diff --no-index a b", "git diff --word-diff", "git diff -w",
    "git show", "git show --stat HEAD", "git show HEAD:src/x.py", "git show --format=%H -s",
    "git blame src/x.py", "git blame -L 10,20 src/x.py", "git shortlog -sn",
    "git describe --tags", "git rev-parse HEAD", "git rev-parse --abbrev-ref HEAD",
    "git rev-parse --show-toplevel", "git rev-parse --is-inside-work-tree",
    "git rev-list --count HEAD", "git ls-files", "git ls-files -m", "git ls-tree -r HEAD",
    "git grep -n foo", "git grep -c needle", "git grep --cached needle", "git grep -e a -e b",
    "git cat-file -p HEAD", "git cat-file -t HEAD", "git merge-base main HEAD",
    "git count-objects -v", "git check-ignore -v x", "git name-rev HEAD",
    "git branch", "git branch -a", "git branch -vv", "git branch --show-current",
    "git branch --contains abc", "git branch --list 'feat*'", "git branch -r --merged",
    "git tag", "git tag -l", "git tag -l 'v1*'", "git tag -n5 -l",
    "git stash list", "git stash show -p", "git remote", "git remote -v",
    "git remote get-url origin", "git reflog", "git reflog show -5",
    "git --no-pager log -1", "git -C /tmp/repo log -1", "git --version",
]


@pytest.mark.parametrize("command", ACCEPT)
def test_the_flags_agents_use_stay_read_only(command):
    assert ok(command), command


# -- any other flag gets a card ---------------------------------------------------------------
# A sample per command: some write or run things, most are simply not on the list.

REJECT = [
    "ls -L", "ls --dereference", "ls --zzz", "ls -la --hyperlinkz",
    "cat -l x", "cat --zzz x",
    "head -f x", "head --zzz x", "tail -f x", "tail -F x", "tail --follow x", "tail --pid=1 x",
    "wc --files0-from=list", "wc -q x",
    "grep -R foo .", "grep --dereference-recursive foo .", "grep -S foo .", "grep -A foo x",
    "grep --zzz foo x", "grep -O foo x",
    "rg -z foo", "rg --search-zip foo", "rg --pre ./p foo", "rg --pre=./p foo",
    "rg -L foo", "rg --follow foo", "rg --hostname-bin=./p foo", "rg --zzz foo",
    "ugrep --pager foo", "ugrep -Q foo", "ugrep --filter=x foo", "ugrep --save-config",
    "sort -o out x", "sort -T /tmp x", "sort --compress-program=gzip x",
    "sort --random-source=x x", "sort --files0-from=x", "sort --out=x x",
    "uniq a b", "uniq -c a b", "cut --zzz x", "tr -x a b", "tr a b c",
    "nl --zzz x", "column -J x", "comm a b c",
    "diff -r a b", "diff --recursive a b", "diff -l a b", "diff --paginate a b", "diff --zzz a b",
    "strings @list", "strings -n 8 @list", "od --zzz x", "hexdump -f fmt x",
    "xxd -r x", "xxd -revert x", "xxd -R always x", "xxd a b", "xxd -ps a b",
    "file -z x", "file -Z x", "file -p x", "file -f list", "file -m magic x",
    "stat --zzz x", "du -L .", "du --dereference .", "du --files0-from=x",
    "df --sync", "md5sum -c sums", "sha256sum --check sums", "shasum -c sums",
    "pwd x", "whoami x", "date 0101", "date -s now", "date -f list", "date -r x",
    "uname -x", "basename --zzz x", "readlink --zzz x", "which --zzz git",
    "echo -x hi", "printf -v name x", "true x", "env", "printenv", "env -i ls", "env -u X ls", "env X=1",
    "command ls", "command -p ls", "id -A",
    "actionlint", "actionlint -oneline .github/workflows/ci.yml",  # may start other linters
    # git
    "git log --output=x", "git log --ext-diff", "git log --textconv", "git log --show-signature",
    "git log --format=%G?", "git log --pretty=format:%GS", "git log -L 1,2:x.c", "git log -Ox",
    "git log --orderfile=x", "git log --zzz", "git log --onel",
    "git diff --ext-diff", "git diff --output=x", "git diff -Ox",
    "git show --show-signature", "git blame --contents x y", "git blame -S revs x",
    "git blame --ignore-revs-file=x y", "git grep -O foo", "git grep -Ox foo",
    "git grep --open-files-in-pager foo", "git grep --textconv foo",
    "git cat-file --filters HEAD:x", "git cat-file --textconv HEAD:x",
    "git branch new", "git branch -D x", "git branch -m a b", "git branch --set-upstream-to=x",
    "git tag v1", "git tag -d v1", "git tag -a v1 -m x", "git tag -v v1",
    "git stash", "git stash pop", "git stash drop", "git remote add x y", "git remote show origin",
    "git remote set-url origin x", "git config user.name x", "git config --unset x",
    "git config --edit", "git config --add a b", "git config --list", "git config -l",
    "git config --get user.name", "git config get user.name", "git config --get http.extraheader",
    "git reflog expire --all", "git reflog delete x",
    "git -c core.pager=x log", "git --git-dir=x log", "git --work-tree=x status",
    "git --exec-path=x status", "git --help", "git fetch", "git checkout x", "git commit -m x",
    "git status --zzz", "git rev-parse --resolve-git-dir x",
]


@pytest.mark.parametrize("command", REJECT)
def test_any_other_flag_gets_a_card(command):
    assert not ok(command), command


# -- operands that would write or run something -----------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        ("xxd dump.bin", True),
        ("xxd dump.bin out.bin", False),  # the second operand is an output file
        ("uniq data.txt", True),
        ("uniq data.txt out.txt", False),  # likewise for uniq
        ("date +%s", True),
        ("date 202001010000", False),  # an operand without `+` sets the clock
        ("tr a b", True),
        ("strings @options.txt", False),  # an `@file` operand is read as more options
        ("command -v ls", True),
        ("command ls", False),  # runs ls
        ("env", False),  # prints every environment variable, secrets included
        ("env ls", False),  # runs ls
        ("printf '%s\\n' a b", True),
    ],
)
def test_operands_that_write_or_run_are_not_read_only(command, expected):
    assert ok(command) is expected, command


# -- sed: print and plain substitution ---------------------------------------------------------


@pytest.mark.parametrize(
    "script",
    [
        "320,345p", "1,20p;40,60p", "/=== Stage 1/,/=== Stage 2/p", "/^static int f(/,/^}/p",
        "/struct A/,/^};/p;/struct B/,/^};/p", "/x/,$p", "$p", "1~2p", "0,/re/p", "/a/,+3p",
        "s/a/b/", "s/a/b/g", "s|/src/x/||", "s/[0-9]*$//", "s/\\(hb_[a-z_]*\\) (.*/\\1/",
        "s/a/b/2", "s/a/b/gI", "s/==[0-9]*==//;s/0x[0-9a-f]*/ADDR/g", "y/abc/xyz/",
        "/x/d", "5q", "=", "/start/,/end/{p}", "/a/!d", "/^$/d",
    ],
)
def test_sed_print_and_substitution_are_accepted(script):
    assert ok(f"sed -n '{script}' data.txt"), script


@pytest.mark.parametrize(
    "script",
    [
        "w out.txt", "1w out.txt", "s/a/b/w out.txt", "s/a/b/e", "e ls", "r /etc/hosts",
        "R x", "W x", "1r x", "/x/w y", "s/[/]/x/", "a text", "i text", "c text",
        ":a;N;ba", "1,5b", "t", "/x/{w y}", "/x/{p", "p}", "s/a/b", "s/a/b/q", "1,p",
        "s/a/b/;w x", "v", "l 5;w x", "#n",
    ],
)
def test_sed_scripts_outside_the_subset_get_a_card(script):
    assert not ok(f"sed -n '{script}' data.txt"), script


def test_sed_checks_every_script_it_is_given():
    assert ok("sed -e 1p -e 's/a/b/' data.txt")
    assert not ok("sed -e 1p -e 'w x' data.txt")
    assert not ok("sed --expression='w x' data.txt")
    assert not ok("sed -i 's/a/b/' x")
    assert not ok("sed --in-place 's/a/b/' x")
    assert not ok("sed -f script.sed x")
    assert ok("sed --sandbox -n 1p x")


# -- awk: printing, matching, arithmetic --------------------------------------------------------


@pytest.mark.parametrize(
    "program",
    [
        "{print $1}", "{print $2, $3, $4}", "NR>1 {print}", "$3 >= 100 {print $1}",
        "{if ($1 > 5) print $1}",
        '{printf "%s:%s ", $1, $2} END {print ""}', '{printf "%d\\t%s\\n", $1+579, substr($0, index($0,$2))}',
        '/^f/{f=1} f{print NR": "$0} f&&/^}/{exit}', "/a/,/b/", "{s+=$1} END {print s}",
        "length($0) > 80", "{a[$1]++} END {for (k in a) print k, a[k]}", "$1 == \"x\" || $2 < 3",
    ],
)
def test_awk_programs_that_only_print_are_accepted(program):
    assert ok(f"awk '{program}' data.txt"), program


@pytest.mark.parametrize(
    "program",
    [
        '{print > "out"}', '{print >> "out"}', '{print | "sort"}', '{print |& "x"}',
        '{print "a;b}" > "out"}', '{print /;/ > "out"}', '{printf("%s", $1) > "out"}',
        'BEGIN{system("ls")}', 'BEGIN{"date" | getline d}', 'BEGIN{getline l < "/etc/hosts"}',
        "BEGIN{print ENVIRON[\"HOME\"]}", "BEGIN{print PROCINFO[\"pid\"]}",
        'BEGIN{ARGV[1]="/etc/hosts"; ARGC=2} {print}', '@include "x"', "BEGIN{f=\"system\"; @f(\"ls\")}",
    ],
)
def test_awk_programs_that_redirect_or_run_get_a_card(program):
    assert not ok(f"awk '{program}' data.txt"), program


def test_awk_flags():
    assert ok("awk -F'|' '{print $1}' data.txt")
    assert ok("awk -F, -v n=2 '{print $n}' data.txt")
    assert not ok("awk -f prog.awk data.txt")
    assert not ok("awk --file=prog.awk data.txt")
    assert not ok("gawk -l ext '{print}' x")
    assert not ok("gawk -e '{print}' x")
    assert not ok("gawk -o out '{print}' x")
    assert not ok("mawk -W exec x")


# -- jq: filters, without modules or the environment ---------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        ("jq . data.json", True),
        ("jq -r '.items[] | .name' data.json", True),
        ("jq '.results | length' data.json", True),
        ("jq -c --arg k v '.[$k]' data.json", True),
        ("jq -s 'add' a.json b.json", True),
        ("jq -n '1+1'", True),
        ("jq --indent 4 . data.json", True),
        ("jq -n env", False),  # the environment is not a local file
        ("jq -n '$ENV.HOME'", False),
        ("jq 'import \"x\" as x; .' data.json", False),  # modules are read from elsewhere
        ("jq 'include \"x\"; .' data.json", False),
        ("jq -L dir . data.json", False),
        ("jq -f prog.jq data.json", False),
        ("jq --from-file prog.jq data.json", False),
        ("jq --zzz . data.json", False),
    ],
)
def test_jq(command, expected):
    assert ok(command) is expected, command


# -- find: predicates that select and print ------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "find . -name '*.py'", "find . -type f -name x", "find src -path '*/test/*' -prune -o -print",
        "find . -newermt 2024-01-01", "find . -not -name x", "find . -maxdepth 2 -type d",
        "find . -printf '%p\\n'", "find . -newer build.log", "find . -iname x", "find . -size -900k",
        "find . -perm -u+x", "find . -user me", "find . -xdev -name x", "find . -mmin -5",
        "find . -print0", "find . \\( -name a -o -name b \\)", "find . ! -name x", "find -H . -name x",
        "find -D search src",
    ],
)
def test_find_predicates_that_only_select_are_accepted(command):
    assert ok(command), command


@pytest.mark.parametrize(
    "command",
    [
        "find . -delete", "find . -exec ls {} ;", "find . -execdir ls ;", "find . -ok ls ;",
        "find . -fprint out", "find . -fprintf out %p", "find . -fls out", "find . -fprint0 out",
        "find . -files0-from list", "find -L . -name x", "find . -follow", "find . -zzz",
        "find . -name", "find . -name x extra",
    ],
)
def test_find_actions_that_write_or_run_get_a_card(command):
    assert not ok(command), command


# -- commands added in OPE-232 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "tree", "tree -L 2", "tree -a -I node_modules src", "tree -d", "tree --gitignore",
        "cmp a b", "cmp -l a b", "sha512sum x", "sha224sum x", "sha384sum x", "b2sum x",
        "cksum x", "sum x", "tac x", "rev x", "fold -w 80 x", "paste -d, a b", "join a b",
        "expand -t 4 x", "unexpand -a x", "seq 10", "seq 1 2 10", "nproc",
    ],
)
def test_added_commands_are_read_only(command):
    assert ok(command), command


@pytest.mark.parametrize(
    "command",
    [
        "tree -o out.txt", "tree -R", "tree -l", "tree --fromfile list", "sha512sum -c sums",
        "b2sum --check sums", "cksum --check sums", "cmp --zzz a b", "seq -f %s --zzz 1",
        "nproc x", "join a b c", "paste --zzz a",
    ],
)
def test_added_commands_keep_their_own_allowlist(command):
    assert not ok(command), command


@pytest.mark.parametrize(
    "command",
    ["less x", "more x", "man ls", "python3 x.py", "sh x", "curl x", "nc x", "xargs ls", "ps aux", "sleep 1"],
)
def test_pagers_interpreters_network_and_runners_stay_out(command):
    assert not ok(command), command


# -- shell parsing ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        ("cat {a,b}.txt", False),  # brace expansion makes more operands than are written
        ("cat x{1..3}", False),
        ("cat '{a,b}'", True),  # quoted: one literal file name
        ("git show stash@{0}", True),  # not an expansion
        ("cat a#b", True),
    ],
)
def test_expansions_the_target_list_cannot_see(command, expected):
    assert ok(command) is expected, command


def test_a_hash_inside_a_word_is_part_of_the_word():
    assert read_targets("cat a#b") == ["a#b"]


# -- read targets: every allowed flag that carries a path ----------------------------------------


@pytest.mark.parametrize(
    "command, path",
    [
        ("grep -f /x/pats foo src", "/x/pats"),
        ("grep --file /x/pats src", "/x/pats"),
        ("grep --exclude-from=/x/l -r foo .", "/x/l"),
        ("rg --ignore-file /x/i foo", "/x/i"),
        ("rg -f /x/pats", "/x/pats"),
        ("rg --files /x", "/x"),  # with --files the first operand is a path, not a pattern
        ("diff --from-file=/x/a b", "/x/a"),
        ("diff --to-file /x/a b", "/x/a"),
        ("diff -X /x/l a b", "/x/l"),
        ("du -X /x/l .", "/x/l"),
        ("du --exclude-from=/x/l .", "/x/l"),
        ("realpath --relative-to=/x a", "/x"),
        ("jq --slurpfile v /x/f.json . d.json", "/x/f.json"),
        ("jq --rawfile v /x/f.txt . d.json", "/x/f.txt"),
        ("find . -newer /x/ref", "/x/ref"),
        ("find . -samefile /x/ref", "/x/ref"),
        ("find . -anewer /x/ref", "/x/ref"),
        ("find . -newermm /x/ref", "/x/ref"),
        ("yamllint -c /x/conf a.yml", "/x/conf"),
        ("git grep -f /x/pats", "/x/pats"),
        ("git ls-files -X /x/l", "/x/l"),
        ("git ls-files --exclude-from=/x/l", "/x/l"),
        ("git diff --no-index /x/a b", "/x/a"),  # git diff reads plain files, too
        ("git -C /x log", "/x"),
    ],
)
def test_every_path_a_flag_carries_is_a_read_target(command, path):
    assert ok(command), command
    assert path in read_targets(command), (command, read_targets(command))


def test_values_that_are_not_paths_are_not_targets():
    assert read_targets("grep --include=*.py -rn foo src") == ["src"]
    assert read_targets("sort -t , -k 2 data.csv") == ["data.csv"]
    assert read_targets("find . -name '*.py'") == ["."]
    assert read_targets("ls --time-style=+%s -l src") == ["src"]


# -- PowerShell: its own commands and parameters ----------------------------------------------------


PS_ACCEPT = [
    "Get-ChildItem", "Get-ChildItem -Recurse -Filter *.py", "ls -Recurse src", "dir src",
    "gci -File -Depth 2", "ls -Name", "Get-ChildItem -Path src -Include *.md -Recurse",
    "Get-Content README.md", "cat README.md", "type README.md", "gc README.md -TotalCount 20",
    "Get-Content -Path README.md -Tail 5", "cat README.md -Head 10", "Get-Content x -Raw",
    "Get-Content x -Encoding utf8", "Select-String -Pattern foo -Path src/*.py",
    "sls foo README.md", "Select-String foo README.md -CaseSensitive -Context 2",
    "cat README.md | Measure-Object -Line", "cat README.md | measure -Word",
    "Get-Location", "pwd", "Test-Path README.md", "Test-Path -Path src -PathType Container",
    "Resolve-Path README.md", "Get-FileHash README.md", "Get-FileHash -Algorithm SHA256 README.md",
    "cat README.md | sort", "cat README.md | Sort-Object -Unique", "ls | Select-Object -First 5",
    "echo hello", "Get-ChildItem -Recurse -ErrorAction SilentlyContinue",
    "git status", "git log --oneline -5", "git diff --stat",
    # a recursive listing may feed cmdlets that only see names and sizes
    "Get-ChildItem -Recurse | Select-Object -First 20", "gci -Recurse -File | Measure-Object",
    "ls -Recurse | Sort-Object Length -Descending | Select-Object -First 5",
    "Get-ChildItem src | Get-Content", "ls *.md | Select-String TODO",
]


@pytest.mark.parametrize("command", PS_ACCEPT)
def test_powershell_read_only_commands(command):
    assert ok(command, "powershell"), command


PS_REJECT = [
    "Get-Content x -Wait", "cat x -Stream s", "Get-ChildItem -FollowSymlink", "ls -la",
    "Get-Content x -OutVariable v", "ls -PipelineVariable p", "ls -ErrorVariable e",
    "ls -ErrorAction Break", "Get-ChildItem -Zzz", "Get-Content x y",
    "Remove-Item x", "rm x", "Set-Content x y", "Out-File x", "Invoke-Expression x", "iex x",
    "Start-Process x", "Invoke-WebRequest x", "curl x", "wget x", "Copy-Item a b",
    "grep foo x", "head -n 5 x", "tail x", "wc -l x", "sed -n 1p x", "find . -name x",
    "cat env:USERPROFILE", "Get-Content -Path Env:PATH", "ls HKLM:/Software", "cat Variable:x",
    "Get-ChildItem Registry::HKEY_LOCAL_MACHINE", "Get-ChildItem -Path Cert:/",
    "git log --output=x", "git branch new", "git config --list",
    # a recursive listing feeding a cmdlet that reads the files it lists
    "Get-ChildItem -Recurse | Select-String secret", "gci -Recurse | Get-Content",
    "ls -r *.txt | sls password", "Get-ChildItem -Depth 3 | Get-Content",
    "gci -Recurse -File | Select-Object -First 5 | Get-Content",
    "Get-ChildItem -s | Get-FileHash",
]


@pytest.mark.parametrize("command", PS_REJECT)
def test_powershell_other_parameters_and_commands_get_a_card(command):
    assert not ok(command, "powershell"), command


@pytest.mark.parametrize(
    "command, path",
    [
        ("Get-Content -Path C:\\x\\a.txt", "C:/x/a.txt"),
        ("Get-Content -LiteralPath C:\\x\\a.txt", "C:/x/a.txt"),
        ("cat C:\\x\\a.txt", "C:/x/a.txt"),
        ("ls -Path:C:\\x", "C:/x"),
        ("Select-String foo C:\\x\\a.txt", "C:/x/a.txt"),
        ("Select-String -Path C:\\x\\a.txt -Pattern foo", "C:/x/a.txt"),
        ("Test-Path C:\\x", "C:/x"),
        ("Resolve-Path C:\\x", "C:/x"),
        ("Get-FileHash C:\\x\\a.txt", "C:/x/a.txt"),
        ("git -C C:\\x log", "C:/x"),
    ],
)
def test_powershell_path_values_are_read_targets(command, path):
    assert ok(command, "powershell"), command
    assert path in read_targets(command, shell="powershell")


def test_powershell_patterns_are_not_targets():
    assert read_targets("Select-String -Pattern C:\\x README.md", shell="powershell") == ["README.md"]
    assert read_targets("Get-ChildItem -Filter *.py src", shell="powershell") == ["src"]
