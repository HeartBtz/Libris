# Libris

Self-hosted literary translation. Libris translates whole books (EPUB, or text chapters for web novels) with your
own language model, keeps each series consistent through a persistent memory (glossary, characters, Book Bible),
and takes a book from import to a validated translated EPUB on its own, while every decision stays visible and
editable.

Your books and translations stay on your server. Text is sent only to the model provider you configure:
OpenAI-compatible, OpenAI, Anthropic, or Codex.

Website: <https://libris-translate.com/>

## Quick start

Libris needs PostgreSQL and a background worker, so start it with the supplied Docker Compose file rather than
`docker run` alone.

You need a Linux AMD64 machine with Docker Engine 26 or newer, Docker Compose v2, Git, 2 GB of free memory and a
language model reachable from the containers.

```bash
git clone https://github.com/HeartBtz/Libris.git
cd Libris
./scripts/install-docker.sh
```

The installer creates a private `.env` with random secrets, pulls `heartbtz/libris:0.7.0` and PostgreSQL, applies
the database migrations, starts the web application and the worker, and waits until they answer. It never replaces
an existing `.env` or deletes data.

Open <http://localhost:8088> and sign in as `admin`. Display the generated password on the server with:

```bash
grep '^BOOTSTRAP_' .env
```

Then add your model in **Settings › LLM providers** and import a first book.

## Image tags

| Tag | Meaning |
| --- | --- |
| `0.7.0` | Exact release. Recommended: pin it in production. |
| `0.7` | Latest patch release of the 0.7 series. |
| `latest` | Latest release. Convenient for a try, not for unattended production. |

```bash
docker pull heartbtz/libris:0.7.0
```

The same image runs the web application and the worker. The optional Codex bridge is built from the source tree.

## Documentation

- [Install with Docker](https://github.com/HeartBtz/Libris/blob/main/docs/docker.md): installation, network and
  HTTPS, updates, troubleshooting
- [Configuration reference](https://github.com/HeartBtz/Libris/blob/main/docs/configuration.md)
- [Backup and restore](https://github.com/HeartBtz/Libris/blob/main/docs/backup.md)
- [Operations](https://github.com/HeartBtz/Libris/blob/main/docs/operations.md): monitoring, retention, costs
- [User guide (French)](https://github.com/HeartBtz/Libris/blob/main/docs/user-guide.fr.md)
- [Changelog](https://github.com/HeartBtz/Libris/blob/main/CHANGELOG.md)

Source code: <https://github.com/HeartBtz/Libris>
License: GNU Affero General Public License v3.0
