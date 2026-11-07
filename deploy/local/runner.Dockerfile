# Build context contains a Linux ARM64 openworker binary (see README).
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
COPY --chmod=0555 openworker /opt/openworker/openworker
# Public sentinel deliberately readable by ordinary DAC but denied by Landlock.
RUN mkdir -p /opt/probe-denied /sandbox && printf 'not-a-secret-boundary-probe\n' > /opt/probe-denied/sentinel && chmod 0644 /opt/probe-denied/sentinel
CMD ["sleep", "infinity"]
