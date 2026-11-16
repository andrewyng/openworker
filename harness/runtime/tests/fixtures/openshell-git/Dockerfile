# Test-only overlay. No package download, real credential or private TLS key.
ARG RUNNER_IMAGE
FROM ${RUNNER_IMAGE}
USER root
COPY --chmod=0555 openworker /opt/openworker/openworker
COPY ca.crt /usr/local/share/ca-certificates/ow-git-fixture.crt
RUN cat /usr/local/share/ca-certificates/ow-git-fixture.crt >> /etc/ssl/certs/ca-certificates.crt
COPY server.py /opt/ow-git-fixture/server.py
