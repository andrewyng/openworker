# syntax=docker/dockerfile:1
# Qualification-only image. Do not publish or use it for production workers.
FROM rust:1.96-bookworm@sha256:a339861ae23e9abb272cea45dfafde21760d2ce6577a70f8a926153677902663 AS build
ENV RUSTUP_TOOLCHAIN=1.96.1 CARGO_BUILD_JOBS=1 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0
WORKDIR /src/harness
COPY harness/ ./
COPY deploy/service/guard/ /src/deploy/service/guard/
COPY deploy/service/platform/ /src/deploy/service/platform/
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/src/harness/target \
    cargo test --locked -p openworker-store-postgres --features test-support --test worker_recovery --no-run --message-format=json > /tmp/artifacts.jsonl && \
    cargo build --locked -p openworker-runtime -p openworker-server-reference --features openworker-runtime/test-fixtures && \
    mkdir /out && cp target/debug/openworker target/debug/openworker-server-reference target/debug/pg-worker-fixture /out/ && \
    test_exe="$(sed -n '/"name":"worker_recovery"/s/.*"executable":"\([^" ]*\)".*/\1/p' /tmp/artifacts.jsonl)" && \
    test -x "$test_exe" && cp "$test_exe" /out/worker-recovery
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3 curl procps && \
    rm -rf /var/lib/apt/lists/* && mkdir /state && chown 1000:1000 /state
COPY --from=build /out/ /out/
COPY --from=build /out/openworker /out/openworker-server-reference /opt/openworker/
COPY --from=build /out/pg-worker-fixture /src/harness/target/debug/pg-worker-fixture
COPY deploy/service/coding-bundle/ /opt/openworker/bundle/
COPY deploy/service/entrypoint.sh /opt/openworker/entrypoint.sh
USER 1000:1000
ENTRYPOINT ["/bin/sh", "/opt/openworker/entrypoint.sh"]
