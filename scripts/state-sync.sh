#!/usr/bin/env bash
# Keep an OpenWorker installation's state IN THIS REPO, so that cloning the
# repo on another box brings the personas, memory, progress and automations
# with it.
#
#   ./scripts/state-sync.sh push    live install  -> state/  (then commit+push)
#   ./scripts/state-sync.sh pull    state/        -> live install
#
# The problem this solves: none of what makes an OpenWorker *yours* is code.
# A fresh clone starts with no personas, empty memory, no progress and an empty
# rail, which looks like a broken build and is really an empty one. Everything
# below lives outside the source tree:
#
#   ~/.config/coworker/personas.json, personas-installed/, persona_connections.json
#   ~/.config/coworker/coworker.db        threads, plans, progress, memories
#   ~/.config/coworker/automation.db      scheduled automations
#   ~/.config/coworker/conversations/     history
#   ~/.config/coworker/mcp.json, prefs.json, config.toml
#   ~/OpenWorker/knowledge/               the brain
#
# DATABASES ARE STORED AS SQL, NOT AS .db FILES. Two reasons. Binary blobs make
# every sync a fresh 6 MB object in git history and never delta-compress; a
# .dump is text that diffs and packs. And the live stores run in WAL mode, so
# recent writes sit in the -wal rather than the .db -- automation.db here was
# 236 KB against a 4.0 MB WAL, and copying the .db alone would have carried a
# fraction of the automations while looking like it worked. `.dump` reads
# through the WAL; a file copy does not.
#
# NEVER SYNCED: secrets.json, openworker.env, sidecar-*.token. Credentials and
# per-install tokens do not belong in a repo, private or not. .gitignore has a
# matching guard so a stray `git add -A` cannot pick them up either.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${COWORKER_STATE_DIR:-$HOME/.config/coworker}"
BRAIN="${OPENWORKER_BRAIN_DIR:-$HOME/OpenWorker/knowledge}"
DEST="$REPO/state"
SERVICES=(openworker-server openworker-ui)

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Copied verbatim. Text, small, and the whole point of the exercise.
PLAIN=(personas.json persona_connections.json mcp.json config.toml
       inbox.json wakes.json prefs.json)
DIRS=(personas-installed conversations)

# openworker-ui declares PartOf=openworker-server, so stopping the server takes
# the UI with it. Record what was running BEFORE stopping anything, or the UI is
# never restarted and the box is left with an agent and no interface.
STOPPED=()
stop_services() {
  STOPPED=()
  local s
  for s in "${SERVICES[@]}"; do
    systemctl --user is-active --quiet "$s" 2>/dev/null && STOPPED+=("$s")
  done
  for s in "${STOPPED[@]:-}"; do
    [ -n "$s" ] || continue
    say "  stopping $s"; systemctl --user stop "$s" || say "  WARN: stop $s failed"
  done
}
start_services() {
  local s
  for s in "${STOPPED[@]:-}"; do
    [ -n "$s" ] || continue
    say "  starting $s"; systemctl --user start "$s" || say "  WARN: start $s failed"
  done
  STOPPED=()
}

do_push() {
  [ -d "$STATE" ] || die "no state directory at $STATE"
  command -v sqlite3 >/dev/null || die "sqlite3 required"

  say "[push] $STATE -> state/"
  mkdir -p "$DEST/coworker" "$DEST/knowledge"

  local f
  for f in "${PLAIN[@]}"; do
    [ -f "$STATE/$f" ] && { cp "$STATE/$f" "$DEST/coworker/$f"; say "  $f"; }
  done
  for f in "${DIRS[@]}"; do
    if [ -d "$STATE/$f" ]; then
      rm -rf "$DEST/coworker/$f"; mkdir -p "$DEST/coworker/$f"
      tar -cf - -C "$STATE/$f" --exclude='*.bak-*' . | tar -xf - -C "$DEST/coworker/$f"
      say "  $f/ ($(find "$DEST/coworker/$f" -type f | wc -l) files)"
    fi
  done

  local db
  for db in "$STATE"/*.db; do
    [ -e "$db" ] || continue
    case "$(basename "$db")" in *.bak-*) continue ;; esac
    local name; name="$(basename "$db")"
    sqlite3 "$db" .dump > "$DEST/coworker/$name.sql"
    local dbsz walsz sqlsz
    dbsz="$(du -h "$db" | cut -f1)"
    walsz="$(du -h "$db-wal" 2>/dev/null | cut -f1 || echo 0)"
    sqlsz="$(du -h "$DEST/coworker/$name.sql" | cut -f1)"
    say "  $name ($dbsz db + $walsz wal) -> $name.sql ($sqlsz)"
  done

  if [ -d "$BRAIN" ]; then
    rm -rf "$DEST/knowledge"; mkdir -p "$DEST/knowledge"
    tar -cf - -C "$BRAIN" . | tar -xf - -C "$DEST/knowledge"
    say "  knowledge/ ($(find "$DEST/knowledge" -type f | wc -l) files)"
  fi

  # A last guard: refuse to leave anything secret-shaped in state/.
  local leaked
  leaked="$(find "$DEST" \( -name 'secrets.json' -o -name '*.token' -o -name 'openworker.env' \) 2>/dev/null || true)"
  [ -z "$leaked" ] || { printf '%s\n' "$leaked"; die "secret-shaped files reached state/; not committing"; }

  say ""
  say "state/ updated. Commit and push, then on the other box:"
  say "  git pull && ./scripts/state-sync.sh pull"
}

do_pull() {
  [ -d "$DEST/coworker" ] || die "no state/ in this repo — run push on the source box first"
  command -v sqlite3 >/dev/null || die "sqlite3 required"

  say "[pull] state/ -> $STATE"
  stop_services

  if [ -d "$STATE" ] && [ -n "$(ls -A "$STATE" 2>/dev/null)" ]; then
    local bak="$STATE.bak-pull-$(date +%Y%m%d-%H%M%S)"
    cp -a "$STATE" "$bak"; say "  existing state -> $bak"
  fi
  mkdir -p "$STATE"

  # Keep this box's own model binding if it has one: it names a provider and a
  # model, and two boxes do not serve the same ones (ollama here, vLLM there).
  # Restoring the other machine's binding does not fail at pull time -- it fails
  # the first time a persona tries to think.
  local keep_models=""
  [ -f "$STATE/prefs.json" ] && keep_models="$(python3 - "$STATE/prefs.json" <<'PY' 2>/dev/null || true
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: sys.exit()
m=d.get("default_model")
if m and m != "REPLACE-ME": print(json.dumps({"models":d.get("models") or [m],"default_model":m}))
PY
)"

  local f
  for f in "$DEST"/coworker/*; do
    local b; b="$(basename "$f")"
    case "$b" in *.sql) continue ;; esac
    if [ -d "$f" ]; then
      rm -rf "$STATE/$b"; mkdir -p "$STATE/$b"
      tar -cf - -C "$f" . | tar -xf - -C "$STATE/$b"
    else
      cp "$f" "$STATE/$b"
    fi
    say "  $b"
  done

  local sql
  for sql in "$DEST"/coworker/*.db.sql; do
    [ -e "$sql" ] || continue
    local name; name="$(basename "$sql" .sql)"
    rm -f "$STATE/$name" "$STATE/$name-wal" "$STATE/$name-shm"
    sqlite3 "$STATE/$name" < "$sql"
    sqlite3 "$STATE/$name" 'PRAGMA integrity_check;' | head -1 | grep -qx ok \
      || die "$name failed integrity_check after restore"
    say "  $name  (rebuilt from SQL, integrity ok)"
  done

  if [ -d "$DEST/knowledge" ]; then
    mkdir -p "$BRAIN"; tar -cf - -C "$DEST/knowledge" . | tar -xf - -C "$BRAIN"
    say "  knowledge/ -> $BRAIN"
  fi

  if [ -n "$keep_models" ]; then
    python3 - "$STATE/prefs.json" "$keep_models" <<'PY' || true
import json,sys
p=sys.argv[1]
d=json.load(open(p)); d.update(json.loads(sys.argv[2]))
json.dump(d,open(p,"w"),indent=2)
PY
    say "  kept this box's model binding"
  else
    say ""
    say "  NOTE: prefs.json carries the SOURCE box's model binding:"
    say "    $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("default_model"))' "$STATE/prefs.json" 2>/dev/null)"
    say "  If this box does not serve that model, set it before use:"
    say "    curl -s localhost:8000/v1/models     # vLLM"
    say "    curl -s localhost:11434/v1/models    # ollama"
  fi

  start_services
  say ""
  say "Pulled. secrets.json / openworker.env are NOT in the repo by design;"
  say "move those separately if this box needs them."
}

case "${1:-}" in
  push) do_push ;;
  pull) do_pull ;;
  *) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
