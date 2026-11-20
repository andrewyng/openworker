FROM python:3.13-slim-bookworm AS runner
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY files.py .
USER 65532:65532
CMD ["sleep", "infinity"]

FROM runner AS service
USER root
RUN pip install --no-cache-dir kubernetes==34.1.0
COPY cluster.py service.py .
USER 65532:65532
CMD ["python", "service.py"]
