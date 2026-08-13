#!/usr/bin/env bash
# Install GUI dependencies with a release-age quarantine while keeping the lockfile frozen.
set -euo pipefail

PROJECT_DIR="${1:-surfaces/gui}"
MIN_AGE_DAYS=7
# npm 11.10 introduced min-release-age. Pin a known-compatible release rather than
# trusting whichever npm happens to ship with the GitHub runner's Node 20 image.
BOOTSTRAP_NPM_VERSION=11.15.0
CUTOFF="$(node -e "console.log(new Date(Date.now() - ${MIN_AGE_DAYS} * 86400000).toISOString())")"

# The runner's bundled npm may predate min-release-age, so quarantine the bootstrap
# package with the older, widely supported --before mechanism.
npm install --global "npm@${BOOTSTRAP_NPM_VERSION}" --before="${CUTOFF}"
hash -r

cd "${PROJECT_DIR}"
if [[ "$(npm config get min-release-age)" != "${MIN_AGE_DAYS}" ]]; then
  echo "::error::npm min-release-age policy is not active"
  exit 1
fi

# npm ci did not originally apply min-release-age. npm install does; the diff check
# retains frozen-lockfile behavior and fails if npm tries to resolve anything new.
npm install
git diff --exit-code -- package-lock.json
