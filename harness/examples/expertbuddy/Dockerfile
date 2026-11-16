FROM node:24-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=optional --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY server ./server
COPY public ./public
ENV NODE_ENV=production INSPECTIONBUDDY_MODE=edge HOST=0.0.0.0 PORT=8080 DATA_DIR=/data
USER node
EXPOSE 8080
CMD ["node", "server/index.mjs"]
