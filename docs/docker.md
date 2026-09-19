# Docker deployment

This guide installs the published Libris image with Docker Compose. It is written for a first self-hosted deployment; no Python or Node.js development environment is required.

## What Docker starts

Libris uses three long-running containers and one short migration task:

| Service | Purpose | Public port |
| --- | --- | --- |
| `api` | Web interface and HTTP API | `8088` by default |
| `worker` | Resumable analysis, translation and review jobs | none |
| `database` | PostgreSQL metadata and job state | none |
| `migrate` | Applies database migrations, then exits successfully | none |

The `api` and `worker` use the same `heartbtz/libris` image. Named Docker volumes retain the database and book files when containers are replaced.

Every container runs without added privileges (`no-new-privileges`, all Linux capabilities dropped; the database keeps the five its entrypoint needs to own its data directory and switch to the `postgres` user) and with a read-only root filesystem. The writable places are the named volumes, a small in-memory `/tmp`, and `/data/tmp` on the books volume for large temporary files (batch export archives, uploads being received). A customised Compose file that mounts other writable paths must declare them as volumes or `tmpfs`.

## Before you begin

Install these tools on a Linux AMD64 machine:

- Docker Engine 26 or newer with the Compose v2 plugin.
- Git, used only to download the Compose file and maintenance scripts.
- At least 2 GB of free RAM and enough disk space for the original books, working data, exports and database backups.
- A language-model endpoint reachable from Docker containers.

Verify Docker before continuing:

```bash
docker version
docker compose version
```

## Install in one command

Clone the public repository and run the installer:

```bash
git clone https://github.com/HeartBtz/Libris.git
cd Libris
./scripts/install-docker.sh
```

The installer creates `.env` with random secrets when needed, pulls `heartbtz/libris:0.6.0`, starts PostgreSQL, applies migrations, and waits for the API health check. It never replaces an existing `.env` or deletes volumes.

Open <http://localhost:8088>. The initial username and password are in `.env`:

```bash
grep '^BOOTSTRAP_' .env
```

Treat this output as a password. Do not paste it into issues, logs or screenshots.

## Manual installation

If you prefer to inspect every command:

```bash
python3 scripts/setup.py
docker compose pull database migrate api worker
docker compose up -d --no-build --wait database migrate api worker
docker compose ps
curl --fail http://127.0.0.1:8088/health
```

If Python is unavailable, the one-command installer runs `setup.py` in a temporary official Python container.

## First model provider

1. Sign in and open **Settings / Paramètres**.
2. Add an OpenAI-compatible or OpenAI Responses endpoint, its model name and API key.
3. Use an address reachable from the containers. `localhost` inside a container is not the Docker host.
4. Test the provider and start with a small EPUB you are allowed to process.
5. Select **internal** memory unless you already operate OpenViking.

Libris itself does not require a GPU when inference runs on another machine or hosted service. Book text selected for inference is sent to the provider you configure.

## LAN and HTTPS access

The default bind is local-only. For a private LAN, edit `.env` and replace the server name with its real hostname or address:

```dotenv
BIND_ADDRESS=0.0.0.0
PORT=8088
ALLOWED_ORIGINS=http://your-server:8088
COOKIE_SECURE=false
```

Apply the change with `docker compose up -d --no-build --wait`. Restrict the port with the host firewall.

For Internet access, keep `BIND_ADDRESS=127.0.0.1`, put Libris behind an HTTPS reverse proxy, set `ALLOWED_ORIGINS` to the exact public HTTPS origin and set `COOKIE_SECURE=true`. The proxy must support long-lived server-sent events and uploads up to your configured `MAX_UPLOAD_MB`. See [installation and HTTPS](installation.md#4-https-reverse-proxy).

## Updates

Pause jobs in the UI and create a backup before changing versions. Then update the repository and image:

```bash
git pull --ff-only
./scripts/install-docker.sh
curl --fail http://127.0.0.1:8088/health
```

The image is pinned in `.env` through `LIBRIS_IMAGE`. Read [CHANGELOG.md](../CHANGELOG.md) before changing that tag. Avoid floating tags in unattended production; `latest` is published for evaluation, not reproducible upgrades.

## Backups

Back up `.env`, PostgreSQL and the complete `/data` volume together. Losing `SECRET_KEY` prevents decryption of saved provider credentials.

```bash
mkdir -p backups
chmod 700 backups
docker compose stop api worker
docker compose exec -T database pg_dump -U translator -d translator -Fc > backups/database.dump
docker compose run --rm --no-deps -T api tar -C /data -czf - . > backups/books.tar.gz
cp .env backups/config.env
chmod 600 backups/*
docker compose up -d --no-build --wait
```

A backup is trustworthy only after a restore test on an isolated Compose project. Full restore instructions are in [installation and recovery](installation.md#backups-and-recovery).

## Useful commands

```bash
docker compose ps
docker compose logs --since=10m api worker
docker compose restart api
docker compose stop
docker compose start
docker compose down
```

`docker compose down` keeps named volumes. Do not add `--volumes` unless you intentionally want to permanently delete the database and all book data after a verified external backup.

## Build from source

Maintainers and contributors can build instead of pulling Docker Hub:

```bash
LIBRIS_IMAGE=epub-translator:local docker compose build --pull api
LIBRIS_IMAGE=epub-translator:local docker compose up -d --no-build --wait
```

The optional Codex bridge is source-built without rebuilding the published application image:

```bash
docker compose --profile codex build codex
docker compose --profile codex up -d --no-build --wait
```

See [Codex connection](codex.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Login returns 403 | `ALLOWED_ORIGINS` must exactly match the browser scheme, host and port. |
| Login immediately disappears | Use `COOKIE_SECURE=true` only through HTTPS. |
| Provider cannot connect | Test the endpoint from the Docker network; do not use container-local `localhost`. |
| Job remains queued | Confirm `worker` is running and the selected provider has available concurrency. |
| Migration container exited | Exit code 0 is expected; inspect `docker compose ps -a migrate`. |
| Export is rejected | Resolve missing or refused passages and review the EPUBCheck report. |
| `Read-only file system` in the logs | A customised setup writes outside the volumes: add a volume or `tmpfs` for that path in the Compose file rather than removing `read_only`. |

When requesting help, include the Libris version, Docker/Compose versions, `docker compose ps`, and bounded logs. Remove credentials, cookies, provider keys and book content first.
