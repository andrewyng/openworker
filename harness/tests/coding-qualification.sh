#!/usr/bin/env bash
# Development acceptance driver. Never provisions cloud resources or loads keys.
set -euo pipefail
set +x
cd "$(dirname "$0")/../.."
export CARGO_INCREMENTAL=0
manifest=harness/Cargo.toml
case "${1:-fast}" in
  fast)
    cargo test --locked --manifest-path "$manifest" -p openworker-runtime --lib \
      --test coding_handoff_config --test coding_handoff_git --test registered_selection
    cargo test --locked --manifest-path "$manifest" -p openworker-git-broker
    (cd harness/typescript-sdk && npm run build && node --test test/coding-presets.test.mjs test/service.test.mjs test/team-placement.test.mjs)
    ;;
  storage)
    : "${OW_PG_TEST_URL:?Provide a disposable PostgreSQL URL; tests create/drop owned schemas}"
    cargo test --locked --manifest-path "$manifest" -p openworker-server-reference \
      --features trusted-git --lib -- --ignored
    cargo test --locked --manifest-path "$manifest" -p openworker-sdk --features test-support \
      --test coding_presets -- --include-ignored
    ;;
  cluster)
    : "${OW_PG_TEST_URL:?Provide a disposable PostgreSQL URL}"
    : "${OW_PROBE_ENDPOINT:?Provide a private local OpenShell HTTPS endpoint}"
    : "${OW_PROBE_CERTS:?Provide the private mTLS certificate directory}"
    : "${OW_PROBE_RUNNER:?Provide the exact Linux runner binary}"
    : "${OW_PROBE_IMAGE:?Provide its immutable digest-pinned image already loaded in the cluster}"
    # Sequential: tests own private schemas/sandboxes and share a bounded gateway.
    for case_name in releases_profiles_provisioning_and_shared_verifier \
        broker::trusted_source_and_registered_publication \
        broker::parallel_mixed_team_qualification; do
      cargo test --locked --manifest-path "$manifest" -p openworker-server-reference \
        --features openshell-test,trusted-git-test --test service_v2 "$case_name" -- --ignored --nocapture
    done
    ;;
  https)
    cargo test --locked --manifest-path "$manifest" -p openworker-git-broker \
      authenticated_https -- --ignored --nocapture
    ;;
  live)
    : "${OW_SERVICE_TEST_BIN:?Build service_v2 first without model credentials, then provide its exact executable path}"
    : "${OW_C7_LIVE_MODEL:?Provide an explicitly authorized available Anthropic model ID}"
    : "${ANTHROPIC_API_KEY:?Provide an authorized key through your environment, never source control}"
    : "${OW_PG_TEST_URL:?Provide a disposable PostgreSQL URL}"
    : "${OW_PROBE_ENDPOINT:?Provide the local OpenShell HTTPS endpoint}"
    : "${OW_PROBE_CERTS:?Provide private mTLS files}"
    : "${OW_PROBE_RUNNER:?Provide the exact Linux runner binary}"
    : "${OW_PROBE_IMAGE:?Provide its immutable digest-pinned image}"
    "$OW_SERVICE_TEST_BIN" broker::bounded_live_coding_team --ignored --nocapture
    ;;
  *) printf 'Usage: %s {fast|storage|cluster|https|live}\n' "$0" >&2; exit 2 ;;
esac
