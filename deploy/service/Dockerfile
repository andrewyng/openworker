# syntax=docker/dockerfile:1
FROM rust:1.96-bookworm@sha256:a339861ae23e9abb272cea45dfafde21760d2ce6577a70f8a926153677902663 AS source
# This pinned image contains 1.96.1. Avoid downloading the workstation override.
ENV RUSTUP_TOOLCHAIN=1.96.1 CARGO_BUILD_JOBS=2
WORKDIR /src/harness
COPY harness/ ./
COPY deploy/service/guard/ /src/deploy/service/guard/
COPY deploy/service/platform/ /src/deploy/service/platform/
FROM source AS build
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/src/harness/target \
    cargo build --locked --release -p openworker-server-reference -p openworker-service-cli -p openworker-runtime && \
    mkdir /out && cp target/release/openworker-server-reference target/release/ows target/release/openworker /out/
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS base
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3 curl && \
    rm -rf /var/lib/apt/lists/* && mkdir /state && chown 1000:1000 /state
COPY deploy/service/bundle/ /opt/openworker/bundle/
COPY deploy/service/entrypoint.sh /opt/openworker/entrypoint.sh
USER 1000:1000
ENTRYPOINT ["/bin/sh","/opt/openworker/entrypoint.sh"]
FROM base AS prebuilt
COPY .ow-service-build/out/ /opt/openworker/
FROM base AS runtime
COPY --from=build /out/ /opt/openworker/
FROM source AS cleanup-build
ARG TARGETARCH
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,id=ow-gcp-target-${TARGETARCH},target=/src/harness/target \
    cargo build --locked --release -p openworker-service-cli --bin ows && \
    mkdir /out && cp target/release/ows /out/
FROM base AS gcp-cleanup
# Same Rust recovery engine; no gcloud, Python, model key or Kubernetes access.
COPY --from=cleanup-build /out/ows /opt/openworker/ows
ENTRYPOINT ["/opt/openworker/ows"]
CMD ["deploy", "gcp", "cleanup", "--help"]
