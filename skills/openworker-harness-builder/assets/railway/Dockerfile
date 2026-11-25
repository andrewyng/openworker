# Adapt the build script and final server entrypoint to the application.
# Install inside Linux: do not copy the developer machine's node_modules.
FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --include=optional
COPY . .
RUN npm run build --if-present && npm prune --omit=dev --include=optional

FROM node:24-bookworm-slim
# The native Linux helper uses OpenSSL 3; slim images do not include it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libssl3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build --chown=node:node /app /app
RUN mkdir -p /data && chown node:node /data
USER node
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8080 OPENWORKER_STATE_DIR=/data/harness
EXPOSE 8080
# Direct exec allows Node to receive SIGTERM and shut down its Rust child.
CMD ["node", "dist/server.js"]
