FROM python:3.13-slim-bookworm AS runner
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY files.py .
USER 65532:65532
CMD ["sleep", "infinity"]

FROM runner AS modal-service
USER root
COPY modal_provider/requirements.txt /tmp/modal-requirements.txt
RUN pip install --no-cache-dir -r /tmp/modal-requirements.txt
COPY service.py errors.py ./
COPY modal_provider/ ./modal_provider/
USER 65532:65532
CMD ["python", "-m", "modal_provider.backend"]

FROM runner AS service
USER root
RUN pip install --no-cache-dir kubernetes==34.1.0
COPY cluster.py service.py errors.py .
USER 65532:65532
CMD ["python", "service.py"]
