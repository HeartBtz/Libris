# Backup and restore

This page is for administrators. It explains what to save, how to take a backup by hand or every night
automatically, how to prove that a backup can be restored, and how to restore an installation or return to the
previous version after a failed update.

A backup is only worth something once you have restored it. Plan a restore test from the start.

## What to save

| What | Where | Why |
| --- | --- | --- |
| `.env` | next to `docker-compose.yml` | Holds `SECRET_KEY`, which decrypts every stored provider key, and the database password. Without it, a restored installation asks for each provider key again. |
| The database | volume `epub-translator_database` | Accounts, series, books, translations, history, glossaries, memory, jobs and settings. Save it with `pg_dump`, not by copying the volume. |
| The books volume | volume `epub-translator_books`, mounted at `/data` | Original EPUBs (`books/`), text and JSON sources (`sources/`), exports, delivered API results and pending imports. Without it, a restored installation can still export text, but not EPUBs, previews or project archives. |
| Codex state (optional) | volume `epub-translator_codex-state` | Sign-in of **Codex · ChatGPT account** providers. Without it, sign in again. |
| OpenViking (optional) | your OpenViking server | Not part of Libris; back it up with its own procedure. Libris can republish its memory from the database. |

The database and the books volume belong together: always restore both from the same backup.

To save a single book with all its work, export it as a project archive instead (**Export › Complete project
(.zip)**) and restore it from the library (**Add content › Restore a Libris archive**).

## Back up by hand

Run these commands in the Libris directory. They stop the web application and the worker for the time of the
copy, so that the database and the files match.

```bash
mkdir -p backups && chmod 700 backups
docker compose stop api worker
docker compose exec -T database sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/database.dump
docker compose run --rm --no-deps -T api tar -C /data -czf - . > backups/books.tar.gz
cp .env backups/config.env
chmod 600 backups/*
docker compose up -d --no-build --wait
```

Copy the `backups` directory to another machine. A backup kept on the same disk does not survive the loss of that
disk.

## Back up every night

`deploy/libris-backup` makes a verified backup while Libris keeps running, writes it to a share mounted from
another machine, and removes old backups. A systemd timer runs it every night. Nothing is installed
automatically.

### Install it

As root on the Docker host, from the Libris directory:

```bash
install -m 0755 deploy/libris-backup deploy/libris-restore /usr/local/sbin/
install -m 0644 deploy/libris-backup.service deploy/libris-backup.timer /etc/systemd/system/
install -m 0600 deploy/libris-backup.conf.example /etc/libris-backup.conf
editor /etc/libris-backup.conf
systemctl daemon-reload
```

1. Mount a share from another machine (NFS, SMB, sshfs…) permanently, for example at `/mnt/libris-backup`, and
   create a destination directory in it. Root must be able to write there, file permissions must be kept (backups
   are created `0600`), and it needs room for as many backups as you retain.
2. Edit `/etc/libris-backup.conf` (see the table below). Set `LIBRIS_BACKUP_MOUNTPOINT` so that the backup fails
   instead of silently filling the local disk when the share is not mounted.
3. Run a first backup and read its result:

   ```bash
   systemctl start libris-backup.service
   journalctl -u libris-backup.service -n 20
   ```

4. Enable the nightly run (03:30, with up to 20 minutes of random delay; a run missed while the machine was off
   happens at the next start):

   ```bash
   systemctl enable --now libris-backup.timer
   systemctl list-timers libris-backup.timer
   ```

5. Watch for failures: check `systemctl is-failed libris-backup.service` in your monitoring, or add an
   `OnFailure=` drop-in that calls your usual notification unit.

### Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIBRIS_BACKUP_DESTINATION` | none (required) | Directory that receives one `libris-<UTC timestamp>/` directory per backup, for example `/mnt/libris-backup/libris`. |
| `LIBRIS_BACKUP_MOUNTPOINT` | empty | When set, the backup fails if this path is not a mounted file system. |
| `LIBRIS_BACKUP_RETENTION_DAYS` | `14` | Backups older than this are removed after each successful backup. |
| `LIBRIS_BACKUP_PROJECT` | `epub-translator` | Compose project of the installation. |
| `LIBRIS_BACKUP_BOOKS_VOLUME` | `<project>_books` | Books volume to archive. |
| `LIBRIS_BACKUP_INCLUDE_ENV` | `false` | `true` copies `.env` into each backup as `config.env`. Only if the destination may hold every secret of the installation. |
| `LIBRIS_BACKUP_SECRET_ENV` | `/opt/epub-translator/.env` | Path of the installation's `.env`, copied when `LIBRIS_BACKUP_INCLUDE_ENV=true`. Set it to your Libris directory's `.env`. |
| `LIBRIS_PRODUCTION_BASE` | `/opt/libris-production` | Where the [production deployment script](operations.md#production-deployment-script) records the deployed version, written into `backup.info`. Harmless if absent. |

When `.env` is not included, save it yourself somewhere safe: it rarely changes.

### What a backup contains

| File | Content |
| --- | --- |
| `database.dump` | `pg_dump -Fc` of the whole database, consistent, taken while Libris runs |
| `books.tar.gz` | the complete books volume |
| `config.env` | a copy of `.env`, only with `LIBRIS_BACKUP_INCLUDE_ENV=true` |
| `backup.info` | date, Compose project, volume, database image and deployed Libris version |
| `SHA256SUMS` | checksums of the files above |

The database is dumped before the books are archived: a book file added in between is harmless, a database row
without its file is not. Both files are then read back entirely before the backup counts. The backup is written
to a hidden `.libris-….partial` directory and renamed only at the end, so an interrupted run never looks like a
backup. Old backups are removed only after a successful one: a failing run never deletes the previous ones. Any
failure makes the systemd unit fail.

## Test a restore every month

`libris-restore` restores a backup into a separate, throwaway Compose project and checks that it works. It never
touches your installation, and it never starts the worker, which would resume the saved jobs and call your model
providers.

```bash
/usr/local/sbin/libris-restore --dry-run /mnt/libris-backup/libris/libris-<timestamp>
/usr/local/sbin/libris-restore /mnt/libris-backup/libris/libris-<timestamp>
```

- `--dry-run` checks the checksums and the archive, and prints each Docker command without running it.
- The real run creates the project `libris-restore-test` with its own volumes, publishes the API on a random
  loopback port, restores the database and the books, runs the migrations and the API, then checks
  `alembic check` and `/health` and prints the number of users, books, passages, book files and source files.
- The project and its volumes are removed at the end. With `--keep` they stay for inspection: find the port with
  `docker compose --project-name libris-restore-test port api 8088` and sign in.

Compare the counts with your installation and write down the date of the test.

The script's defaults match the [production deployment script](operations.md#production-deployment-script). For
an installation made with `install-docker.sh`, point it at your Libris directory:

```bash
cd /path/to/Libris
LIBRIS_RESTORE_COMPOSE_FILE="$PWD/docker-compose.yml" \
LIBRIS_RESTORE_ENV_FILE="$PWD/.env" \
LIBRIS_RESTORE_IMAGE="$(grep '^LIBRIS_IMAGE=' .env | cut -d= -f2)" \
  /usr/local/sbin/libris-restore /mnt/libris-backup/libris/libris-<timestamp>
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIBRIS_RESTORE_PROJECT` | `libris-restore-test` | Name of the throwaway project. The production project name is refused. |
| `LIBRIS_RESTORE_COMPOSE_FILE` | `$LIBRIS_PRODUCTION_BASE/docker-compose.yml` | Compose file to use. |
| `LIBRIS_RESTORE_ENV_FILE` | the backup's `config.env`, else `LIBRIS_PRODUCTION_SECRET_ENV` (`/opt/epub-translator/.env`) | Configuration to use. |
| `LIBRIS_RESTORE_IMAGE` | the image recorded by the last production deployment | Application image to restore with. Use the version that made the backup, or a newer one. |
| `LIBRIS_PRODUCTION_PROJECT` | `epub-translator` | The project the script refuses to touch. |

## Restore an installation

Do this after losing data, or to return to the state before a failed update. Use the **same `.env`** (at least
the same `SECRET_KEY`) and an application version at least as recent as the one that made the backup.

1. Pause running books in the interface if Libris still runs.
2. Check the backup:

   ```bash
   backup=/mnt/libris-backup/libris/libris-<timestamp>
   (cd "$backup" && sha256sum --check SHA256SUMS)
   ```

3. From the Libris directory, stop the application, then restore the database:

   ```bash
   docker compose stop api worker
   docker compose exec -T database sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error' < "$backup/database.dump"
   ```

4. Restore the books too if they were lost or damaged. This **replaces** the whole content of the volume:

   ```bash
   docker compose run --rm --no-deps -T api sh -c 'find /data -mindepth 1 -delete && tar -C /data -xzf -' < "$backup/books.tar.gz"
   ```

5. Start Libris again and check it:

   ```bash
   docker compose up -d --no-build --wait
   docker compose exec -T api alembic check
   ```

   Then sign in, open a book preview, test a provider connection and export an EPUB.

If `.env` was lost too, restore it first from `config.env` if your backups include it. Otherwise create a new one
with `python3 scripts/setup.py` (the database password must then be the one of the restored volume) and enter
every provider key again.

To rebuild on a new machine, install Libris with the same version, copy the saved `.env` in place before the first
start, start only the database (`docker compose up -d --wait database`), then follow steps 3 to 5.

Never run `pg_restore --clean` against an installation whose current data you want to keep.

## Roll back a failed update

Database migrations only go forward. To return to the previous version after an update that changed the database
schema, you need the database as it was before the update.

**Installation made with `install-docker.sh`:**

1. Stop the application: `docker compose stop api worker`.
2. Restore the database backup taken before the update (step 3 above). The books volume normally does not need
   restoring.
3. Start the previous version:

   ```bash
   git checkout v<previous version>
   LIBRIS_IMAGE=heartbtz/libris:<previous version> ./scripts/install-docker.sh
   ```

   Go back to the matching files too: a later run of the installer from newer files would move `.env` to the
   release those files ship.

4. Check `/health`, sign in and resume the books.

**Installation managed by the production deployment script:** each deployment dumps the database just before
migrating, into `/opt/libris-production/backups/pre-<timestamp>-<commit>.dump` (the last five are kept), and
keeps the images it replaced.

- If the failed version did not change the schema, run `libris-production-deploy --rollback`: it restarts the
  previous images. It refuses when the schema differs, without changing anything.
- Otherwise, restore the dump taken just before the faulty deployment (step 3 above, with the Compose options
  shown in [operations](operations.md#production-deployment-script)), skip the books, then run
  `libris-production-deploy --rollback`.

The pre-deployment dumps stay on the same disk as the installation: they protect an update, not the machine. Keep
the nightly backup as well.
