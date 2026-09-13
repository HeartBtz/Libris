FROM node:22-bookworm-slim AS codex
WORKDIR /opt/codex
COPY codex_bridge/package*.json ./
RUN npm ci --omit=dev

FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 libpcre2-8-0 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /opt/codex /opt/codex
RUN ln -s /opt/codex/node_modules/.bin/codex /usr/local/bin/codex && codex --version \
    && pip install --no-cache-dir fastapi==0.141.1 uvicorn==0.52.4
WORKDIR /service
COPY codex_bridge/ /service/codex_bridge/
RUN useradd --uid 10002 --create-home bridge && mkdir /state && chown bridge:bridge /state && chmod -R a+rX /service
USER bridge
CMD ["uvicorn", "codex_bridge.app:app", "--host", "0.0.0.0", "--port", "8092", "--no-access-log", "--timeout-graceful-shutdown", "15"]
