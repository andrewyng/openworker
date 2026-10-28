FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914
WORKDIR /app
COPY --chown=node:node server.mjs /app/server.mjs
COPY --chown=node:node public /app/public
USER node
EXPOSE 8080
ENTRYPOINT ["node", "/app/server.mjs"]
CMD ["/config/ui.json"]
