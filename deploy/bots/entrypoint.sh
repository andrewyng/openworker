#!/bin/sh
set -eu
umask 077

# Identity, conversations, model keys and browser profile all live on this
# runtime's private volume. No public API or browser-debugging port is opened.
if [ -f "$COWORKER_STATE_DIR/remote.json" ]; then
    exec openworker up
fi
: "${BOT_CONTROLLER:?Set the HTTPS controller address}"
: "${BOT_RUNTIME_NAME:?Set the runtime name}"
exec openworker join "$BOT_CONTROLLER" --name "$BOT_RUNTIME_NAME"
