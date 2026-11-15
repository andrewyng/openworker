# Test harness only, not the shipped ExampleApp artifact.
FROM docker:29-cli AS docker-cli
FROM node:20-bookworm-slim
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl socat \
    && rm -rf /var/lib/apt/lists/*
ARG TARGETARCH
RUN case "$TARGETARCH" in arm64) arch=aarch64 ;; amd64) arch=x86_64 ;; *) exit 1 ;; esac \
    && artifact="openshell-${arch}-unknown-linux-musl.tar.gz" \
    && curl -fsSL "https://github.com/NVIDIA/OpenShell/releases/download/v0.0.116/${artifact}" -o /tmp/openshell.tgz \
    && curl -fsSL https://github.com/NVIDIA/OpenShell/releases/download/v0.0.116/openshell-checksums-sha256.txt -o /tmp/checksums \
    && expected="$(awk -v name="$artifact" '$2 == name || $2 == "*"name {print $1}' /tmp/checksums)" \
    && test -n "$expected" \
    && printf '%s  /tmp/openshell.tgz\n' "$expected" | sha256sum -c - \
    && tar -xzf /tmp/openshell.tgz -C /usr/local/bin openshell \
    && chmod +x /usr/local/bin/openshell
CMD ["node", "tests/linux-client/entry.mjs"]
