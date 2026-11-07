# Build from the repository root, with a native Linux helper matching TARGETARCH.
FROM node:24-bookworm-slim AS build
WORKDIR /src
COPY harness/typescript-sdk/package*.json harness/typescript-sdk/
RUN npm --prefix harness/typescript-sdk ci
COPY harness/typescript-sdk/ harness/typescript-sdk/
RUN npm --prefix harness/typescript-sdk run build
COPY harness/examples/quickstart/ harness/examples/quickstart/
WORKDIR /src/harness/examples/quickstart
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1
RUN npm ci --ignore-scripts && npm run build && npm prune --omit=dev && npm --prefix ../../typescript-sdk prune --omit=dev

FROM node:24-bookworm-slim
WORKDIR /app
COPY --from=build /src/harness /harness
# Supply this file from a matching OpenWorker Linux release/build before docker build.
COPY harness/examples/quickstart/bin/openworker-harness /usr/local/bin/openworker-harness
RUN chmod 755 /usr/local/bin/openworker-harness && mkdir /state && chown node:node /state
USER node
ENV HOST=0.0.0.0 PORT=8080 OPENWORKER_STATE_DIR=/state OPENWORKER_EXECUTABLE=/usr/local/bin/openworker-harness
WORKDIR /harness/examples/quickstart
EXPOSE 8080
CMD ["node","web/server.mjs"]
