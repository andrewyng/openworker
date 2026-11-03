#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
level=${1:-fast}
features=test-support,guardrails,compliance,security-adapters,compliance-export
case "$level" in
  fast)
    cargo test --offline -p openworker-guardrails -p openworker-compliance -p openworker-security-adapters
    cargo test --offline -p openworker-runtime --features compliance-export --lib
    cargo test --offline -p openworker-sdk --features "$features" --lib safety_callbacks
    cargo test --offline -p openworker-sdk --features "$features" --test safety --test security_scanner --test compliance_export --test tool_free
    cargo test --offline -p openworker-runtime --test core
    cargo test --offline -p openworker-guardrails --no-default-features
    cargo check --offline -p openworker-sdk --no-default-features
    target=$(cargo metadata --offline --format-version=1 --no-deps | node -e 'let s="";process.stdin.on("data",c=>s+=c);process.stdin.on("end",()=>process.stdout.write(JSON.parse(s).target_directory));')
    export OW_TS_TEST_HOST="$target/debug/sdk-test-host"
    (cd typescript-sdk && npm run build && node --test test/safety.test.mjs test/tool-free.test.mjs)
    (cd service-ui && node --test test/compliance.test.mjs test/server.test.mjs)
    ;;
  storage)
    : "${OW_PG_TEST_URL:?Use a disposable local PostgreSQL database}"
    cargo test --offline -p openworker-store-postgres --features guardrails,compliance --test compliance -- --ignored
    cargo test --offline -p openworker-store-postgres --features guardrails,compliance --test controls safety_resume -- --ignored
    cargo test --offline -p openworker-sdk --features "$features" --test safety -- --ignored
    cargo test --offline -p openworker-server-reference --features compliance-export --test compliance_api -- --ignored
    ;;
  cluster)
    : "${OW_PG_TEST_URL:?Use a disposable local PostgreSQL database}"
    : "${OW_PROBE_ENDPOINT:?Explicit local OpenShell HTTPS endpoint required}"
    : "${OW_PROBE_CERTS:?Existing test mTLS certificate directory required}"
    : "${OW_PROBE_RUNNER:?Exact Linux ToolRunner binary required}"
    : "${OW_PROBE_IMAGE:?Matching immutable image already available in the cluster required}"
    cargo test --offline -p openworker-store-postgres --features guardrails,compliance,openshell-test --test safety_openshell -- --ignored --nocapture
    ;;
  ui)
    : "${OW_PG_TEST_URL:?Use a disposable local PostgreSQL database}"
    : "${OW_PLAYWRIGHT_MODULE:?Absolute path to an installed Playwright module required}"
    export OW_COMPLIANCE_UI_SCRIPT="$PWD/service-ui/test/compliance-live.mjs"
    cargo test --offline -p openworker-server-reference --features compliance-export --test compliance_api -- --ignored --nocapture
    ;;
  *) echo "Usage: $0 fast|storage|cluster|ui" >&2; exit 2 ;;
esac
