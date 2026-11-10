#!/bin/sh
set -eu
# The all-in-one image invokes Grafana directly (not its Docker entrypoint),
# so explicitly load the mounted secret. Never pass the password in argv.
GF_SECURITY_ADMIN_PASSWORD=$(cat /run/secrets/grafana_password)
export GF_SECURITY_ADMIN_PASSWORD
exec /otel-lgtm/run-all.sh
