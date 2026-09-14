FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS runtime
ARG LIBRIS_VERSION=0.2.1
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Libris" \
      org.opencontainers.image.version="${LIBRIS_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/HeartBtz/Libris" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless curl unzip libpcre2-8-0 && rm -rf /var/lib/apt/lists/*
ARG EPUBCHECK_VERSION=5.3.0
ARG EPUBCHECK_SHA256=6c07e68584b2e2ce2f89fe06e1246dfead3eb36b46b340e7d93524f29dcff6c5
RUN curl --fail --location --retry 5 "https://github.com/w3c/epubcheck/releases/download/v${EPUBCHECK_VERSION}/epubcheck-${EPUBCHECK_VERSION}.zip" -o /tmp/epubcheck.zip \
    && echo "${EPUBCHECK_SHA256}  /tmp/epubcheck.zip" | sha256sum --check - \
    && unzip -q /tmp/epubcheck.zip -d /opt && rm /tmp/epubcheck.zip
ENV EPUBCHECK_JAR=/opt/epubcheck-${EPUBCHECK_VERSION}/epubcheck.jar
WORKDIR /app/backend
COPY scripts/harden_epubcheck.py /tmp/harden_epubcheck.py
RUN python /tmp/harden_epubcheck.py /opt/epubcheck-5.3.0 && rm /tmp/harden_epubcheck.py
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
RUN pip uninstall -y pip
COPY backend/ ./
COPY prompts/ /app/prompts/
COPY --from=frontend /build/dist /app/frontend/dist
RUN useradd --uid 10001 --create-home translator && mkdir -p /data/books /data/projects /data/exports \
    && chown -R translator:translator /data && chmod -R a+rX /app
USER translator
EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
