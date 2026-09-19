# Libris

Self-hosted, context-aware EPUB translation with resumable jobs, persistent book memory, human review and EPUBCheck validation.

Libris uses your own OpenAI-compatible, OpenAI Responses or optional Codex provider. Your books and translation history stay on your server; selected text is sent only to the provider you configure.

## Quick start

Libris needs PostgreSQL and a background worker, so use the supplied Docker Compose configuration instead of starting this image with `docker run` alone.

Requirements: a Linux AMD64 machine with Docker Engine 26 or newer, Docker Compose v2, Git, at least 2 GB of free RAM and a language-model endpoint reachable from containers.

```bash
git clone https://github.com/HeartBtz/Libris.git
cd Libris
./scripts/install-docker.sh
```

The installer:

- creates a private `.env` with random secrets and an initial password;
- pulls `heartbtz/libris:0.6.0` and PostgreSQL;
- applies database migrations;
- starts the web API and resumable worker;
- waits for the health check;
- preserves an existing `.env` and existing Docker volumes.

Open <http://localhost:8088>. Sign in as `admin`; display the generated initial credentials locally with:

```bash
grep '^BOOTSTRAP_' .env
```

Do not share that output. After signing in, open **Settings / Paramètres**, add your model endpoint, model name and API key, test the provider, and import a small EPUB you are allowed to process. Select **internal** memory unless you already run OpenViking.

## Verify the installation

```bash
docker compose ps
curl --fail http://127.0.0.1:8088/health
docker compose logs --since=10m api worker
```

The expected health response contains `"status":"ok"` and the installed version.

## LAN or Internet access

The default installation listens only on the Docker host. For private LAN access, set `BIND_ADDRESS=0.0.0.0`, the correct `ALLOWED_ORIGINS`, and `COOKIE_SECURE=false` in `.env`, then run:

```bash
docker compose up -d --no-build --wait
```

For Internet access, keep Libris bound to `127.0.0.1`, use an HTTPS reverse proxy, set the exact public HTTPS origin in `ALLOWED_ORIGINS`, and set `COOKIE_SECURE=true`.

## Updates

Read the changelog, then update the Compose files and pinned image safely:

```bash
git pull --ff-only
./scripts/install-docker.sh
curl --fail http://127.0.0.1:8088/health
```

Never add `--volumes` to `docker compose down` during an update. Named volumes contain the PostgreSQL database and book data. Back up `.env`, PostgreSQL and `/data` together; losing `SECRET_KEY` prevents decryption of saved provider credentials.

## Image tags

- `0.4.1`: exact release, recommended for reproducible deployments.
- `0.5`: latest patch in the 0.5 series.
- `latest`: latest stable release, convenient for evaluation but not recommended for unattended production.

Pull the exact application image manually with:

```bash
docker pull heartbtz/libris:0.6.0
```

The same image runs the web API and worker with different Compose commands. It is not a complete standalone deployment without PostgreSQL and persistent volumes.

## Documentation

- [Complete Docker guide](https://github.com/HeartBtz/Libris/blob/main/docs/docker.md)
- [Installation, HTTPS, backup and recovery](https://github.com/HeartBtz/Libris/blob/main/docs/installation.md)
- [French user guide](https://github.com/HeartBtz/Libris/blob/main/docs/user-guide.fr.md)
- [Changelog](https://github.com/HeartBtz/Libris/blob/main/CHANGELOG.md)
- [Support](https://github.com/HeartBtz/Libris/blob/main/SUPPORT.md)

Source code: <https://github.com/HeartBtz/Libris>  
License: GNU Affero General Public License v3.0
