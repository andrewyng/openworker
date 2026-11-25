# Build context: deploy/workspaces with a matching Linux openworker binary at its root.
FROM node:24-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c
USER root
RUN apt-get update && apt-get install --no-install-recommends -y python3 ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/starter
COPY nodejs/package.json nodejs/package-lock.json ./
RUN npm ci --omit=dev && ln -s /opt/starter/node_modules /node_modules && ln -s /opt/starter/node_modules/.bin/vite /usr/local/bin/vite && npm cache clean --force
COPY --chmod=0555 openworker /opt/openworker/openworker
COPY files.py runner_call.py /app/
ENV PATH="/opt/starter/node_modules/.bin:${PATH}" HOME=/tmp
WORKDIR /data
USER 65532:65532
CMD ["sleep", "infinity"]
