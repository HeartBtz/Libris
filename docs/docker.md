# Install Libris with Docker

This guide is for anyone who wants to run Libris on their own machine or server. You do not need to know Python
or Node.js: Docker does the work. By the end you will have Libris running, signed in, connected to a language
model and translating a first book. Later sections cover network access, HTTPS, updates, removal and common
problems.

## What you need

- A **Linux machine (x86-64 / AMD64)** with at least 2 GB of free memory, and disk space for your books, their
  translations, exports and backups.
- **Docker Engine 26 or newer** with the **Compose v2** plugin. Follow
  [Docker's installation guide](https://docs.docker.com/engine/install/) for your distribution.
- **Git**, to download Libris.
- **A language model** Libris can reach over the network: a hosted service with an API key (OpenAI, Anthropic, or
  any OpenAI-compatible provider) or your own inference server. Libris itself needs no GPU.

Check Docker before you continue:

```bash
docker version
docker compose version
```

Libris sends the text of your books to the model provider you choose, and nowhere else. Everything else (books,
translations, history) stays on your server.

## Install

Download Libris and run the installer:

```bash
git clone https://github.com/HeartBtz/Libris.git
cd Libris
./scripts/install-docker.sh
```

The installer:

1. creates `.env`, the configuration file, with random secrets and an initial administrator password (only if
   `.env` does not exist yet; it never replaces yours);
2. downloads the application image `heartbtz/libris:0.6.0` and PostgreSQL;
3. starts the database, updates its schema, then starts the web application and the background worker;
4. waits until Libris answers, then prints its address.

It takes a few minutes the first time. It never deletes data, so you can run it again safely.

If you prefer to run each step yourself:

```bash
python3 scripts/setup.py
docker compose pull database migrate api worker
docker compose up -d --no-build --wait database migrate api worker
```

### What is running

| Service | Role |
| --- | --- |
| `api` | Web interface and HTTP API, published on port `8088` of the host (loopback only by default) |
| `worker` | Runs analysis, translation and review jobs in the background; resumes them after a restart |
| `database` | PostgreSQL 17: accounts, books, translations and job state |
| `migrate` | Updates the database schema, then stops. Seeing it as "exited (0)" is normal |

Your data lives in two Docker volumes, `epub-translator_database` and `epub-translator_books`. They survive
container updates and restarts. The Compose project is named `epub-translator`; keep that name, because another
name would start with empty volumes.

The containers run with a read-only file system and no extra privileges. The only writable places are the
volumes and a small temporary area.

## Sign in

Open <http://localhost:8088> on the machine where Libris runs. The username is `admin`; the password was generated
for you. Display both with:

```bash
grep '^BOOTSTRAP_' .env
```

Treat this output as a password: do not paste it into issues, chats or screenshots. Change the password after
your first sign-in from your account page.

The interface is in French or English; switch with the language button in the top bar.

## Connect a language model

1. Open **Settings › LLM providers** and click **New provider**.
2. Choose the **Connection / protocol**: *OpenAI-compatible · Chat Completions* for most services and local
   servers, *Anthropic · Claude (API key)*, *OpenAI · Chat Completions (API key)*, *Codex / OpenAI · API key
   (Responses)*, or *Codex · ChatGPT account* (needs the optional bridge, see [Codex](codex.md)).
3. Enter the **Base URL** (for example `https://api.example.com/v1`), the **API key** and the **Model**.
4. Set the **Context window** and **Maximum output tokens** to your model's real limits, and **Concurrent books**
   to how many books this provider may work on at once.
5. Click **Save**, then **Test / detect models**.

The address must be reachable **from the containers**. Inside a container, `localhost` is the container itself,
not your machine. For a model server on the same Linux host, use the host's network address, or add
`extra_hosts: ["host.docker.internal:host-gateway"]` to both `api` and `worker` in a Compose override file and use
`http://host.docker.internal:<port>`.

A successful model list does not prove that translation works: try a short book first.

API keys are stored encrypted with `SECRET_KEY` and never sent back to the browser.

## Translate a first book

1. In the library, click **Add content** and choose **EPUB books** (or **TXT chapters** for a web novel). Pick
   where the book goes: an existing series, a new series or a standalone volume.
2. Open the book and click **Configure the book**: choose the provider, the languages and the quality level.
   Leave the memory on **internal** unless you run [OpenViking](openviking.md).
3. Click **Start the autopilot**. Libris analyses the book, translates it, reviews it and settles open points on
   its own; the [autopilot](autopilot.md) page explains every stage.
4. When it is done, click **Download the EPUB**.

The full user guide, in French, is [user-guide.fr.md](user-guide.fr.md).

## Open Libris to your network

By default Libris only listens on the machine itself (`127.0.0.1`). Settings mentioned here are described in
the [configuration reference](configuration.md).

### On a private network (plain HTTP)

Edit `.env`, replacing `your-server` with the machine's name or IP address. `COOKIE_SECURE=false` is needed here
because the browser refuses `Secure` cookies over plain HTTP on any address other than `localhost`:

```dotenv
BIND_ADDRESS=0.0.0.0
PORT=8088
ALLOWED_ORIGINS=http://your-server:8088
COOKIE_SECURE=false
```

Apply the change:

```bash
docker compose up -d --no-build --wait
```

Anyone who can reach that port can see the sign-in page: restrict it with the host firewall.
`ALLOWED_ORIGINS` only checks which web page sent a request; it is not a firewall.

### On the Internet (HTTPS)

Keep Libris on `127.0.0.1` and put an HTTPS reverse proxy in front of it. In `.env`:

```dotenv
BIND_ADDRESS=127.0.0.1
ALLOWED_ORIGINS=https://books.example.com
COOKIE_SECURE=true
FORWARDED_ALLOW_IPS=172.18.0.1
```

`FORWARDED_ALLOW_IPS` is the address from which the proxy's requests reach the container, so that Libris reads
each visitor's real address (used for logs and to slow down password guessing). For a proxy on the same host it
is usually the gateway of the Compose network; the access lines of `docker compose logs api` show which address
the requests come from.

The proxy must:

- pass the browser's `Origin` and `Host` headers unchanged, and set `X-Forwarded-For` and `X-Forwarded-Proto`;
- accept request bodies up to `MAX_UPLOAD_MB` (60 MB by default);
- keep long-lived connections open and unbuffered for live progress (`/api/projects/<id>/events`, server-sent
  events).

A minimal nginx example (certificates configured as usual for your server):

```nginx
server {
    listen 443 ssl;
    server_name books.example.com;

    client_max_body_size 64m;

    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

A proxy running in a container cannot use its own `localhost` to reach Libris: give it a shared Docker network or
the host's address.

## Update

Read the [changelog](../CHANGELOG.md) first, then:

1. Pause running books in the interface (they would resume anyway, from their last checkpoint).
2. Make a backup: see [backup and restore](backup.md).
3. Update the files, then run the installer again:

   ```bash
   git pull --ff-only
   ./scripts/install-docker.sh
   curl --fail http://127.0.0.1:8088/health
   ```

   Add `--profile codex` to the installer if you use the Codex bridge.

The image is pinned in `.env` (`LIBRIS_IMAGE=...`). When `.env` names an official release
(`heartbtz/libris:<x.y.z>`) older than the one shipped with your copy of Libris, the installer moves it to the
shipped release and says so. It keeps a newer release, and any other image you wrote there (a local build, another
registry, another tag). To install a specific image, name it on the command line:
`LIBRIS_IMAGE=heartbtz/libris:<version> ./scripts/install-docker.sh`; the installer writes it into `.env`.
Database migrations run automatically before the web application starts. Resume the paused books afterwards.

To go back to a previous version after a schema change, you need the backup taken before the update: see
[backup and restore](backup.md). For finer control over restarts (updating the web application without
interrupting running jobs), see [operations](operations.md#update-libris).

## Everyday commands

```bash
docker compose ps                              # state of each service
docker compose logs --since=10m api worker     # recent logs
docker compose restart api                     # restart the web application
docker compose stop                            # stop Libris, keep everything
docker compose start                           # start it again
curl --fail http://127.0.0.1:8088/health       # {"status":"ok","version":"..."}
```

## Uninstall

Stop Libris and remove its containers, **keeping** your data:

```bash
docker compose down
```

To delete everything for good (database, books, translations), only after a backup you have verified:

```bash
docker compose --profile codex down --volumes --remove-orphans
docker image rm heartbtz/libris:0.6.0
```

Check with `docker volume ls` that no `epub-translator_*` volume is left, then delete the `Libris` directory,
which contains `.env`.

## Build from source

Contributors can build the image instead of downloading it:

```bash
LIBRIS_IMAGE=epub-translator:local docker compose build --pull api
LIBRIS_IMAGE=epub-translator:local docker compose up -d --no-build --wait
```

See [development](development.md) for tests and the development setup.

## Supported environments

| Environment | Status |
| --- | --- |
| Linux x86-64 (AMD64) with Docker Engine and Compose v2 | Supported and tested |
| Ubuntu, Debian | Supported with Docker's official packages |
| PostgreSQL 17 (bundled) | Supported and tested; the only database for Docker installations |
| Chromium-based desktop browsers, and mobile layouts | Tested |
| Firefox, Safari, physical mobile devices | Expected to work, not tested |
| Fedora, RHEL, Rocky, AlmaLinux | Not tested; SELinux may need local configuration |
| Windows and macOS with Docker Desktop, WSL 2 | Not tested; keep data in Docker volumes, not in Windows folders |
| Linux ARM64 | Not tested; images are published for AMD64 only |
| Kubernetes | Not supported: no manifests are provided |

**Books.** Reflowable EPUB 3 is the main target. EPUB 2, fixed-layout, right-to-left and DRM-protected books are
not supported; encrypted archive entries are refused. Text volumes can also be imported as TXT, Markdown, HTML or
DOCX chapters, or as JSON through the [automation API](api.md). EPUBCheck validates the structure of an exported
EPUB, not the quality of its translation.

**Project archives.** Libris restores project archives (**Export › Complete project (.zip)**) written
by any earlier version. Archives written by this version cannot be read by versions older than 0.6.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The page does not load | `docker compose ps`: `api` must be `healthy`. Check `BIND_ADDRESS`, `PORT` and the firewall. |
| Sign-in answers "origin not allowed" (403) | `ALLOWED_ORIGINS` must match the address in the browser exactly: scheme, host and port, no trailing slash. |
| Sign-in seems to do nothing, you stay on the login page | You open Libris over plain HTTP on a network address (not `localhost`) with `COOKIE_SECURE=true`: set it to `false`, or use HTTPS. `http://localhost:8088` works with `true`. |
| The API does not start: `SECRET_KEY` or `BOOTSTRAP_PASSWORD` error | `.env` is missing values: generate one with `python3 scripts/setup.py` (it refuses to overwrite an existing `.env`). |
| `migrate` shows "exited" | Normal when the exit code is 0. Otherwise read `docker compose logs migrate`. |
| The provider test fails | Test the URL from a container, not from your browser; `localhost` means the container itself. |
| A book stays queued | The worker must be running (`docker compose ps worker`) and the provider must have free **Concurrent books** capacity. |
| A book is "waiting" | The provider is unavailable; Libris retries on its own. See [operations](operations.md#troubleshooting). |
| Upload refused as too large | Raise `MAX_UPLOAD_MB` (and your proxy's limit). |
| `Read-only file system` in the logs | A customised Compose file writes outside the volumes: add a volume or `tmpfs` for that path instead of removing `read_only`. |

When you ask for help (see [SUPPORT.md](../SUPPORT.md)), include the Libris version, `docker version`,
`docker compose ps` and a few minutes of logs. Remove passwords, API keys, cookies and book text first.
