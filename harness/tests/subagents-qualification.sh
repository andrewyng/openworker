#!/usr/bin/env bash
# Local qualification only. Never loads keys or provisions cloud resources.
set -euo pipefail
set +x
cd "$(dirname "$0")/../.."
export CARGO_INCREMENTAL=0
case "${1:-fast}" in
  fast)
    cargo test --locked --manifest-path harness/Cargo.toml -p openworker-runtime \
      --test standalone_subagents --test agent_team_storage --test team_type_catalog
    cargo test --locked --manifest-path harness/Cargo.toml -p openworker-sdk --features test-support \
      --test standalone_subagents --test team_types --test tool_free
    npm --prefix harness/typescript-sdk test
    ;;
  managed)
    : "${OW_TS_TEST_HOST:?Build sdk-test-host and provide its absolute path}"
    npm --prefix harness/typescript-sdk run build
    node --test harness/typescript-sdk/test/standalone-subagents.test.mjs
    ;;
  storage)
    : "${OW_PG_TEST_URL:?Provide a disposable PostgreSQL URL}"
    cargo test --locked --manifest-path harness/Cargo.toml -p openworker-store-postgres \
      --test standalone_subagents -- --ignored
    ;;
  cluster)
    : "${OW_PG_TEST_URL:?Provide a disposable PostgreSQL URL}"
    : "${OW_PROBE_ENDPOINT:?Provide a local OpenShell endpoint}"
    : "${OW_PROBE_CERTS:?Provide the private mTLS directory}"
    : "${OW_PROBE_IMAGE:?Provide the immutable Linux runner image}"
    : "${OW_PROBE_RUNNER:?Provide the matching Linux runner binary}"
    cargo test --locked --manifest-path harness/Cargo.toml -p openworker-server-reference \
      --features openshell-test --test service_v2 standalone_children_share_openshell_without_a_public_team -- --ignored --nocapture
    ;;
  live)
    : "${OW_SERVICE_TEST_BIN:?Build service_v2 first without credentials; provide its exact executable}"
    : "${OW_STANDALONE_LIVE_MODEL:?Provide an authorized Anthropic model ID}"
    : "${ANTHROPIC_API_KEY:?Provide an authorized key in the environment}"
    : "${OW_PG_TEST_URL:?Provide a disposable PostgreSQL URL}"
    : "${OW_PROBE_ENDPOINT:?Provide a local OpenShell endpoint}"
    : "${OW_PROBE_CERTS:?Provide the private mTLS directory}"
    : "${OW_PROBE_IMAGE:?Provide the immutable Linux runner image}"
    : "${OW_PROBE_RUNNER:?Provide the matching Linux runner binary}"
    "$OW_SERVICE_TEST_BIN" standalone_live_app_builder --ignored --nocapture
    ;;
  *) printf 'Usage: %s {fast|managed|storage|cluster|live}\n' "$0" >&2; exit 2 ;;
esac
