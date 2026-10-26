# OpenWorker's CLI in an image for OpenShell (https://github.com/NVIDIA/OpenShell):
#
#   openshell sandbox create --from ghcr.io/andrewyng/openworker:latest \
#       --provider <your model provider> -- openworker run --prompt "..."
#
# What OpenShell asks of an image: a standard Linux base, a non-root USER, and iproute2
# (nftables lets it also log direct connections). The rest is the CLI and the tools an
# agent's commands usually reach for. See docs/openshell-inside.md.
#
# Published by .github/workflows/image.yml. To build your own, from the repository root, so
# the CLI in the image is this checkout:
#   docker build -f packaging/openshell/Dockerfile -t openworker:local .

FROM node:22-bookworm-slim AS node

FROM python:3.13-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git openssh-client \
        iproute2 nftables \
        ripgrep fd-find jq less nano tree procps \
        unzip zip xz-utils make build-essential \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

# GitHub's own CLI: with OpenShell's GitHub provider attached, `gh` is how an agent
# reaches GitHub (the credential is swapped in for `gh` and `git` only).
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Node and npm, from Node's own image.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN pip install --no-cache-dir uv

# The CLI lives in a virtual environment of its own, apart from the Python an agent's
# project uses. Two things about where:
# - under /usr, because OpenShell's default policy lets a sandbox read /usr and not /opt
#   (with the CLI in /opt the sandbox could not even start it: "Permission denied");
# - `--copies`, so the environment has its own interpreter file and an OpenShell provider
#   profile can name OpenWorker (/usr/local/lib/openworker/venv/bin/python) and not every
#   Python program in the sandbox.
COPY pyproject.toml README.md LICENSE /tmp/openworker-src/
COPY coworker /tmp/openworker-src/coworker
RUN python -m venv --copies /usr/local/lib/openworker/venv \
    && /usr/local/lib/openworker/venv/bin/pip install --no-cache-dir /tmp/openworker-src \
    && ln -s /usr/local/lib/openworker/venv/bin/openworker /usr/local/bin/openworker \
    && rm -rf /tmp/openworker-src

# The account agents run as, with /sandbox as its home and the place work happens.
RUN groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid sandbox --home-dir /sandbox --create-home --shell /bin/bash sandbox
WORKDIR /sandbox
USER sandbox
