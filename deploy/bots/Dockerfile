FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    COWORKER_STATE_DIR=/state \
    COWORKER_DEDICATED_BOT_RUNTIME=1 \
    HOME=/state/home

WORKDIR /opt/openworker
COPY pyproject.toml README.md ./
COPY coworker ./coworker
RUN pip install --no-cache-dir '.[browser,messaging]' \
    && python -m playwright install --with-deps chromium \
    && useradd --uid 1000 --create-home bot \
    && mkdir -p /state/home \
    && chown -R bot:bot /state \
    && chmod -R a+rX /opt/playwright
COPY deploy/bots/entrypoint.sh /usr/local/bin/bot-entrypoint
RUN chmod 755 /usr/local/bin/bot-entrypoint
USER bot
ENTRYPOINT ["bot-entrypoint"]
