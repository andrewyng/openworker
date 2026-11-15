#!/bin/sh
set -eu
# Installer only; no Docker socket, no privilege escalation in runtime roles.
mkdir -p /service /gateway /ingress /postgres /runner /bootstrap
cp /input/config.json /service/config.json
cp /input/database-url /service/database-url
cp /input/pg-ca.crt /service/pg-ca.crt
chown -R 1000:1000 /service
chmod 0500 /service
chmod 0400 /service/*
cp /input/openshell/* /gateway/
chown -R 1000:1000 /gateway
chmod 0500 /gateway
chmod 0400 /gateway/*
cp /input/api.crt /ingress/tls.crt
cp /input/api.key /ingress/tls.key
cp /input/nginx.conf /ingress/nginx.conf
chown -R 101:101 /ingress
chmod 0500 /ingress
chmod 0400 /ingress/*
cp /input/pg.crt /postgres/tls.crt
cp /input/pg.key /postgres/tls.key
cp /input/pg-password /postgres/password
chown -R 70:70 /postgres
chmod 0500 /postgres
chmod 0400 /postgres/*
cp /input/admin-token /bootstrap/admin-token
chown -R 1000:1000 /bootstrap
chmod 0500 /bootstrap
chmod 0400 /bootstrap/*
cp /input/runner /runner/openworker
chown -R 1000:1000 /runner
chmod 0555 /runner /runner/openworker

mkdir -p /models
for provider in openai anthropic; do
  if [ -f "/input/model-$provider" ]; then cp "/input/model-$provider" "/models/$provider"; fi
done
chown -R 1000:1000 /models
chmod 0500 /models
find /models -type f -exec chmod 0400 {} \;
