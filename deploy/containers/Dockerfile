# syntax=docker/dockerfile:1
FROM rust:1.96-bookworm@sha256:a339861ae23e9abb272cea45dfafde21760d2ce6577a70f8a926153677902663 AS source
ENV CARGO_BUILD_JOBS=2
WORKDIR /src/harness
COPY harness/ /src/harness/
COPY harness/tests/contracts/ /src/harness/tests/contracts/
FROM source AS source-check
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/src/harness/target \
    cargo check --locked -p openworker-server-reference -p openworker-runtime
FROM source AS build
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/src/harness/target \
    cargo build --locked --release -p openworker-server-reference -p openworker-runtime && \
    mkdir /out && cp target/release/openworker target/release/openworker-server-reference /out/
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS base
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3 curl && \
    rm -rf /var/lib/apt/lists/* && mkdir -p /state /opt/openworker && chown 1000:1000 /state
WORKDIR /opt/openworker
ARG COWORKER_BUNDLE=harness/examples/assistant
COPY ${COWORKER_BUNDLE}/ /opt/openworker/bundle/
USER 1000:1000
ENTRYPOINT ["/opt/openworker/openworker-server-reference"]
# Local qualification can reuse an explicitly built/stripped Linux build.
FROM base AS prebuilt
COPY .ow-helm/build/openworker .ow-helm/build/openworker-server-reference /opt/openworker/
FROM base AS runtime
COPY --from=build /out/ /opt/openworker/
