# Installing Libris

## 1. Local configuration

Run `python3 scripts/setup.py` in the repository root. It generates independent application, database, bootstrap and Codex bridge secrets. Do not commit `.env` or replace it during upgrades. For an existing installation the script intentionally fails instead of replacing keys.

| Setting                                    | Meaning                                                                  |
| ------------------------------------------ | ------------------------------------------------------------------------ |
| `BIND_ADDRESS`                             | Interface on the Docker host; defaults to `127.0.0.1`                    |
| `PORT`                                     | Published HTTP port; defaults to `8088`                                  |
| `ALLOWED_ORIGINS`                          | Comma-separated browser origins including scheme and port, without paths |
| `COOKIE_SECURE`                            | Use `true` behind HTTPS, `false` for local HTTP                          |
| `SECRET_KEY`                               | Persistent application encryption key; retain with backups               |
| `BOOTSTRAP_USERNAME`, `BOOTSTRAP_PASSWORD` | First administrator, created only on an empty database                   |
| `POSTGRES_PASSWORD`                        | PostgreSQL initialization and connection password                        |
| `CODEX_BRIDGE_TOKEN`                       | Private API-to-bridge authentication secret                              |
| `OPENVIKING_URL`, `OPENVIKING_API_KEY`     | Optional external memory service                                         |

Changing bootstrap credentials does not reset an existing account. Changing the database password in `.env` does not change an initialized PostgreSQL role's password.

## 2. Start and verify

```bash
docker compose up -d --build --wait
docker compose ps
curl --fail http://127.0.0.1:8088/health
```

The migration service exits successfully after upgrading the database. This is expected. API health does not prove that a model provider or OpenViking is available: test those separately in Settings.

## 3. LAN access

Set these example values to your own server address:

```dotenv
BIND_ADDRESS=0.0.0.0
PORT=8088
ALLOWED_ORIGINS=http://your-server:8088
COOKIE_SECURE=false
```

Apply with `docker compose up -d --wait`. Restrict network access with your host firewall. `ALLOWED_ORIGINS` is an origin check, not a firewall.

## 4. HTTPS reverse proxy

Use your own DNS name and a reverse proxy that supports long-lived HTTP/SSE connections. For a proxy on the same host, keep the loopback bind and configure:

```dotenv
ALLOWED_ORIGINS=https://books.example.com
COOKIE_SECURE=true
```

Forward `books.example.com` to `http://127.0.0.1:8088`. Preserve the browser Origin header, allow your chosen upload size (default application limit: 60 MiB), disable buffering for `/api/projects/*/events`, and allow long request timeouts. Set TLS certificates through your proxy's normal mechanism. A containerized proxy must use an accessible host address or a shared Docker network; its own `localhost` is not the Libris host.

## 5. Connect a model

OpenAI-compatible providers accept an endpoint such as `https://provider.example/v1`. A local inference server can be reached through a LAN address accessible from the containers. `localhost` in a container refers to that container, not the host. For Linux host inference, an optional Compose override can add `extra_hosts: ["host.docker.internal:host-gateway"]` to **both api and worker**; use that hostname in the provider URL.

Declare only the JSON/reasoning capabilities your provider supports, and set a context window and output budget within its actual limits. A successful model-list test alone does not verify structured translation responses. Try a small EPUB first.

No external memory service is required. Choose `internal` memory in the book configuration for a standalone installation. Configure OpenViking only if you have your own instance.

### Optional Codex / ChatGPT

New setup files include a private bridge token. Existing installations can use `python3 scripts/enable_codex.py` as described in [Codex connection](codex.md).

```bash
docker compose --profile codex up -d --build --wait
```

Complete the official login flow in the provider settings. Keep the bridge port private and include its state volume in backups if you want to preserve authentication. Account eligibility and service limits are determined by the provider.

## Updates

Back up first. Pause jobs in the UI, then:

```bash
git pull --ff-only
docker compose up -d --build --wait
```

Include `--profile codex` if you use Codex. Check health and resume jobs. Database migrations run before the API starts. Do not use `down -v`. To roll back a schema-changing update, restore the matching database/files/configuration backup as well as the old application revision.

The Compose project name remains `epub-translator` for compatibility with existing installations. Changing it or using a different `-p` name selects different volumes; do not rename a deployed project casually.

## Backups and recovery

For a consistent backup, pause jobs and stop `api` and `worker` during capture. Store backups outside the repository with restricted permissions.

```bash
mkdir -p backups
chmod 700 backups
docker compose stop api worker
docker compose exec -T database pg_dump -U translator -d translator -Fc > backups/database.dump
docker compose run --rm --no-deps -T api tar -C /data -czf - . > backups/books.tar.gz
cp .env backups/config.env
chmod 600 backups/*
docker compose up -d --wait
```

If using Codex, also back up the `codex-state` named volume with the bridge stopped. External OpenViking requires its own backup procedure.

Restore into an isolated installation with the **same application revision and saved `.env`**. Start the database, restore the dump using `pg_restore -U translator -d translator --clean --if-exists`, extract the books archive into the `/data` volume, then start the application. Test login, book preview, provider decryption and EPUB export before using the recovered instance. A backup file's presence alone is not a restore test.

## Troubleshooting

- **403 on login:** browser origin must exactly match an `ALLOWED_ORIGINS` entry. Check scheme, hostname and port.
- **Login does not persist:** `COOKIE_SECURE=true` requires HTTPS.
- **502/provider error:** check inference availability from both API and worker; a proxy's `/models` endpoint may work while inference fails.
- **Job queued:** check that provider's “Livres simultanés” limit and active jobs.
- **Switch provider mid-book:** pause, save the new provider, then resume.
- **Export rejected:** inspect missing/refused passages and EPUBCheck output; partial source-retaining export is explicit.

Use bounded logs for diagnosis: `docker compose logs --since=5m api worker`. Review logs before sharing; never publish `.env`, cookies, authentication headers or complete model requests containing private books.
