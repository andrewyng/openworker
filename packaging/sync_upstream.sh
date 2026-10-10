#!/usr/bin/env bash
# Sync andrewyng/openworker into the Chinese i18n branch, bump the fork version,
# re-assert fork updater endpoints, and optionally create a release tag.
#
# Usage:
#   packaging/sync_upstream.sh                  # latest upstream vX.Y.Z tag
#   packaging/sync_upstream.sh v0.2.2           # explicit tag
#   FORCE=1 packaging/sync_upstream.sh          # rebuild even if already on that upstream
#   DRY_RUN=1 packaging/sync_upstream.sh        # print plan only
#   CREATE_TAG=1 packaging/sync_upstream.sh     # push a v*-zh.N tag (triggers Release)
#
# Env:
#   UPSTREAM_REMOTE   default: upstream
#   UPSTREAM_URL      default: https://github.com/andrewyng/openworker.git
#   BRANCH            default: i18n-simplified-chinese
#   FORCE             1 = allow zh.N bump on the same upstream base
#   CREATE_TAG        1 = create + push the release tag after a successful sync
#   DRY_RUN           1 = do not mutate git state

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-upstream}"
UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/andrewyng/openworker.git}"
BRANCH="${BRANCH:-i18n-simplified-chinese}"
FORCE="${FORCE:-0}"
CREATE_TAG="${CREATE_TAG:-0}"
DRY_RUN="${DRY_RUN:-0}"
REQUESTED_TAG="${1:-}"

if ! git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
  git remote add "$UPSTREAM_REMOTE" "$UPSTREAM_URL"
fi
git fetch "$UPSTREAM_REMOTE" --tags --force

if [ -n "$REQUESTED_TAG" ]; then
  TAG="$REQUESTED_TAG"
else
  TAG="$(
    git tag -l 'v*' --sort=-v:refname \
      | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
      | head -1
  )"
fi

if [ -z "${TAG:-}" ]; then
  echo "error: no upstream vX.Y.Z tag found" >&2
  exit 1
fi

if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG does not exist after fetch" >&2
  exit 1
fi

UPSTREAM_VERSION="${TAG#v}"
CURRENT_VERSION="$(
  python3 -c "import json; print(json.load(open('surfaces/gui/src-tauri/tauri.conf.json'))['version'])"
)"

NEXT_VERSION="$(
  CURRENT_VERSION="$CURRENT_VERSION" UPSTREAM_VERSION="$UPSTREAM_VERSION" FORCE="$FORCE" python3 - <<'PY'
import os, re, sys

cur = os.environ["CURRENT_VERSION"]
upstream = os.environ["UPSTREAM_VERSION"]
force = os.environ.get("FORCE", "0") == "1"

m = re.fullmatch(r"([0-9]+\.[0-9]+\.[0-9]+)(?:-zh\.([0-9]+))?", cur)
if not m:
    print(f"error: unrecognised current version {cur!r}", file=sys.stderr)
    sys.exit(2)
cur_base, cur_n = m.group(1), int(m.group(2) or 0)

if cur_base == upstream:
    if cur_n == 0:
        # Plain upstream version still in conf — first zh build.
        print(f"{upstream}-zh.1")
    elif force:
        print(f"{upstream}-zh.{cur_n + 1}")
    else:
        print(f"SKIP already synced to upstream {upstream} as {cur}", file=sys.stderr)
        print("")
        sys.exit(0)
else:
    # New upstream base → start a fresh zh series.
    print(f"{upstream}-zh.1")
PY
)"

if [ -z "$NEXT_VERSION" ]; then
  echo "already up to date with $TAG ($CURRENT_VERSION); set FORCE=1 to cut a new zh build"
  exit 0
fi

echo "plan:"
echo "  upstream tag : $TAG"
echo "  branch       : $BRANCH"
echo "  version      : $CURRENT_VERSION -> $NEXT_VERSION"
echo "  create tag   : $CREATE_TAG"
echo "  dry run      : $DRY_RUN"

if [ "$DRY_RUN" = "1" ]; then
  exit 0
fi

git checkout "$BRANCH"
# Keep our commits on top of the upstream release tag.
if ! git merge --no-edit "$TAG"; then
  echo "error: merge conflict while syncing $TAG" >&2
  echo "resolve conflicts, then re-run:" >&2
  echo "  python3 packaging/apply_fork_updater.py --version $NEXT_VERSION" >&2
  echo "  git add -A && git commit" >&2
  echo "  CREATE_TAG=1 packaging/sync_upstream.sh $TAG" >&2
  exit 1
fi

python3 packaging/apply_fork_updater.py --version "$NEXT_VERSION"

# README release pointer (best-effort; ignore if the section was removed).
if grep -q 'releases/tag/v' README.md; then
  python3 - <<PY
from pathlib import Path
import re
p = Path("README.md")
text = p.read_text()
text2 = re.sub(
    r"https://github.com/cr-yijieshusheng/openworker/releases/tag/v[^\s)]+",
    f"https://github.com/cr-yijieshusheng/openworker/releases/tag/v$NEXT_VERSION",
    text,
    count=1,
)
if text2 != text:
    p.write_text(text2)
    print("updated README release link")
PY
fi

git add surfaces/gui/src-tauri/tauri.conf.json README.md packaging/apply_fork_updater.py || true
if git diff --cached --quiet; then
  echo "warning: merge brought no conf drift to commit; continuing"
else
  git commit -m "chore(fork): sync $TAG → $NEXT_VERSION (fork updater)"
fi

if [ "$CREATE_TAG" = "1" ]; then
  git tag -a "v$NEXT_VERSION" -m "OpenWorker 简体中文 $NEXT_VERSION (upstream $TAG)"
  git push origin "$BRANCH"
  git push origin "v$NEXT_VERSION"
  echo "pushed tag v$NEXT_VERSION — Release workflow should start"
else
  echo "synced locally. To publish:"
  echo "  git push origin $BRANCH"
  echo "  git tag -a v$NEXT_VERSION -m 'OpenWorker 简体中文 $NEXT_VERSION'"
  echo "  git push origin v$NEXT_VERSION"
fi
