# Build from this example directory. npm selects the target Linux runtime.
FROM node:24-bookworm-slim AS build
WORKDIR /app
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1
COPY package.json package-lock.json ./
RUN npm ci
COPY src ./src
COPY tsconfig.json ./
RUN npm run build && npm prune --omit=dev

FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app/package*.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY web ./web
COPY public ./public
RUN mkdir /state && chown node:node /state
USER node
ENV HOST=0.0.0.0 PORT=8080 OPENWORKER_STATE_DIR=/state
EXPOSE 8080
CMD ["node", "web/server.mjs"]
