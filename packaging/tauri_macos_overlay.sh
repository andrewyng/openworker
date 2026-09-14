#!/usr/bin/env bash
# Print the Tauri config overlay needed for this macOS build, or nothing when the
# stock config is sufficient. Kept separate so the release-policy matrix is unit-testable.
set -euo pipefail

HAS_IDENTITY=false
HAS_UPDATER_KEY=false
[ -n "${APPLE_SIGNING_IDENTITY:-}" ] && HAS_IDENTITY=true
[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ] && HAS_UPDATER_KEY=true

if [ "$HAS_IDENTITY" = false ] && [ "$HAS_UPDATER_KEY" = true ]; then
  printf '%s' '{"bundle":{"createUpdaterArtifacts":true,"macOS":{"signingIdentity":"-"}}}'
elif [ "$HAS_IDENTITY" = false ]; then
  printf '%s' '{"bundle":{"macOS":{"signingIdentity":"-"}}}'
elif [ "$HAS_UPDATER_KEY" = true ]; then
  printf '%s' '{"bundle":{"createUpdaterArtifacts":true}}'
fi
