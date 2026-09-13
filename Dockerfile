FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless curl unzip && rm -rf /var/lib/apt/lists/*
ARG EPUBCHECK_VERSION=5.3.0
RUN curl -fSL "https://github.com/w3c/epubcheck/releases/download/v${EPUBCHECK_VERSION}/epubcheck-${EPUBCHECK_VERSION}.zip" -o /tmp/epubcheck.zip \
    && unzip -q /tmp/epubcheck.zip -d /opt && rm /tmp/epubcheck.zip
ENV EPUBCHECK_JAR=/opt/epubcheck-5.3.0/epubcheck.jar
WORKDIR /app/backend
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/ ./
COPY prompts/ /app/prompts/
COPY --from=frontend /build/dist /app/frontend/dist
RUN useradd --uid 10001 --create-home translator && mkdir -p /data/books /data/projects /data/exports \
    && chown -R translator:translator /data && chmod -R a+rX /app
USER translator
EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
