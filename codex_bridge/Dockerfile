FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS codex
WORKDIR /opt/codex
COPY codex_bridge/package*.json ./
RUN npm ci --omit=dev

FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
ARG LIBRIS_VERSION=0.3.5
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Libris Codex bridge" \
      org.opencontainers.image.version="${LIBRIS_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/HeartBtz/Libris" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 libpcre2-8-0 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /opt/codex /opt/codex
RUN ln -s /opt/codex/node_modules/.bin/codex /usr/local/bin/codex && codex --version \
    && pip install --no-cache-dir fastapi==0.141.1 uvicorn==0.52.4 \
    && pip uninstall -y pip
WORKDIR /service
COPY codex_bridge/ /service/codex_bridge/
RUN useradd --uid 10002 --create-home bridge && mkdir /state && chown bridge:bridge /state && chmod -R a+rX /service
USER bridge
CMD ["uvicorn", "codex_bridge.app:app", "--host", "0.0.0.0", "--port", "8092", "--no-access-log", "--timeout-graceful-shutdown", "15"]
