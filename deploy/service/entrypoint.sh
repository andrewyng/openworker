#!/bin/sh
set -eu
# Passwords never appear in Compose environment or process arguments.
export OW_PG_URL="$(cat /run/service/database-url)"
if [ "${1:-}" = service-worker ]; then
  if [ -f /models/openai ]; then export OPENAI_API_KEY="$(cat /models/openai)"; fi
  if [ -f /models/anthropic ]; then export ANTHROPIC_API_KEY="$(cat /models/anthropic)"; fi
fi
if [ "${1:-}" = bootstrap ]; then
  /opt/openworker/openworker-server-reference --config /run/service/config.json migrate
  exec /opt/openworker/openworker-server-reference --config /run/service/config.json auth-bootstrap
fi
exec /opt/openworker/openworker-server-reference --config /run/service/config.json "$@"
