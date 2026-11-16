#!/bin/sh
# Synthetic MCP used only by feature tests. A workspace tag changes discovery
# without changing the pinned executable. Never shipped in partner bundles.
set -eu
cd "$1"
while IFS= read -r line; do
  id=$(printf '%s' "$line" | sed -n 's/.*"id":\([0-9][0-9]*\).*/\1/p')
  [ -n "$id" ] || continue
  case "$line" in
    *'"method":"initialize"'*)
      result='{"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"catalog-fixture","version":"1"}}' ;;
    *'"method":"tools/list"'*)
      if [ -f catalog-hang ]; then
        : > catalog-started
        sleep 60
      fi
      description=original
      if [ -f catalog-changed ]; then description=changed; fi
      result='{"tools":[{"name":"example_echo","description":"'"$description"'","annotations":{"readOnlyHint":true},"inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}}]}' ;;
    *'"method":"tools/call"'*)
      : > unexpected-execution
      result='{"content":[{"type":"text","text":"fixture executed"}],"isError":false}' ;;
    *) exit 3 ;;
  esac
  printf '{"jsonrpc":"2.0","id":%s,"result":%s}\n' "$id" "$result"
done
