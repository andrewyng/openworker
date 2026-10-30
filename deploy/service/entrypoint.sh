#!/bin/sh
set -eu
# Passwords never appear in Compose environment or process arguments.
export OW_PG_URL="$(cat /run/service/database-url)"
if [ -f /run/observability/database-url ]; then
  export OW_OBSERVABILITY_URL="$(cat /run/observability/database-url)"
fi
if [ "${1:-}" = service-worker ]; then
  if [ -f /models/openai ]; then export OPENAI_API_KEY="$(cat /models/openai)"; fi
  if [ -f /models/anthropic ]; then export ANTHROPIC_API_KEY="$(cat /models/anthropic)"; fi
fi
if [ "${1:-}" = bootstrap ]; then
  /opt/openworker/openworker-server-reference --config /run/service/config.json migrate
  exec /opt/openworker/openworker-server-reference --config /run/service/config.json auth-bootstrap
fi
exec /opt/openworker/openworker-server-reference --config /run/service/config.json "$@"
