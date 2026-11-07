FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
WORKDIR /app
COPY --chown=1000:1000 harness/task-desk/server.mjs /app/server.mjs
COPY --chown=1000:1000 harness/task-desk/public/ /app/public/
USER 1000:1000
ENV NODE_ENV=production
EXPOSE 54350
ENTRYPOINT ["node","/app/server.mjs"]
CMD ["/config/task-desk.json"]
