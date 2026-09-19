# Scheduled backup and restore

A deployment only dumps the database just before it migrates, on the production disk, and keeps the last five dumps. That protects a release, not the installation: the books volume is never saved, and losing the disk loses everything. The scheduled backup covers that.

## What a backup contains

`deploy/libris-backup` writes one directory per run, `libris-<UTC timestamp>/`, in a destination that should be a share mounted from another host:

| File | Content |
| --- | --- |
| `database.dump` | `pg_dump -Fc` of the whole database, transactionally consistent, taken while Libris keeps running |
| `books.tar.gz` | the complete `/data` volume (`<project>_books`): original EPUBs (`books/`), TXT chapter files and JSON payloads of text volumes (`sources/<project>/`), project files, exports, `staging/` (files of imports not confirmed yet, removed at confirmation or expiry: harmless but not needed) and `results/` (stored results of automation requests; rebuilt from the database when missing) |
| `config.env` | only with `LIBRIS_BACKUP_INCLUDE_ENV=true`: a copy of the installation's `.env`. It is left out by default because it holds every secret; without its `SECRET_KEY`, a restore on another host asks for the provider keys again |
| `backup.info` | date, Compose project, volume, database image, deployed Libris version |
| `SHA256SUMS` | checksums of the files above |

The database is dumped before the books are archived: a book file written in between is harmless, a database row without its file is not. A backup only counts once both files have been read back entirely (`pg_restore` over the whole dump, `tar -t` over the whole archive); it is written as a hidden `.libris-….partial` directory and renamed at the end, so an interrupted run never looks like a backup. Only then are backups older than `LIBRIS_BACKUP_RETENTION_DAYS` (14 by default) removed: a failing run never deletes the previous ones. Any failure exits non-zero, which marks the systemd unit as failed.

The source files are needed for EPUB exports, previews and project archives: without `books/` and `sources/`, a restored installation still exports text (TXT, Markdown, chapter ZIP) and the Book Bible, but refuses those with an explicit message. A single volume can also be saved on its own as a project archive (version 3, see [architecture](architecture.md#archive-de-projet-version-3)).

The optional Codex bridge state (`codex-state` volume) and an external OpenViking instance are not included; see [installation](installation.md#backups-and-recovery).

## Installation on the production host

Nothing in the repository installs itself. On CT116, as root:

```bash
install -m 0755 deploy/libris-backup deploy/libris-restore /usr/local/sbin/
install -m 0644 deploy/libris-backup.service deploy/libris-backup.timer /etc/systemd/system/
install -m 0600 deploy/libris-backup.conf.example /etc/libris-backup.conf
editor /etc/libris-backup.conf
```

1. Mount a share of another host (NFS, SMB, sshfs…) permanently, for instance at `/mnt/libris-backup`, and create the destination directory in it. The share must let root write, keep permissions (files are created `0600`) and have room for `RETENTION_DAYS` times a backup. Set `LIBRIS_BACKUP_MOUNTPOINT` to the mount point: if the share is not mounted, the backup fails instead of silently filling the production disk.
2. Run one backup by hand and read its result: `systemctl start libris-backup.service && journalctl -u libris-backup.service -n 20`.
3. Enable the daily run (03:30, up to 20 minutes later; a run missed while the host was off happens at the next boot): `systemctl enable --now libris-backup.timer`, then check `systemctl list-timers libris-backup.timer`.
4. Watch for failures: `systemctl is-failed libris-backup.service` in the host monitoring, or an `OnFailure=` drop-in towards the usual notification unit.

## Monthly restore test

A backup that was never restored is a hope, not a backup. Once a month, and after any change to this procedure:

```bash
/usr/local/sbin/libris-restore --dry-run /mnt/libris-backup/ct116/libris-<timestamp>
/usr/local/sbin/libris-restore /mnt/libris-backup/ct116/libris-<timestamp>
```

The dry run verifies the checksums and the archive and prints every Docker command without starting anything. The real run restores into a separate Compose project, `libris-restore-test` (`LIBRIS_RESTORE_PROJECT`; the production project name is refused), with its own volumes and the API published only on a random loopback port. It uses the deployed application image (`LIBRIS_RESTORE_IMAGE` to choose another) and the backup's `config.env` when present, otherwise the installation's `.env` (`LIBRIS_RESTORE_ENV_FILE` to choose). It starts the database, restores the dump, extracts the books, runs the migrations and the API — never the worker, which would resume the backed-up jobs and call the model providers — then checks `alembic check`, `/health`, and prints the number of users, books, passages, book files and source files. The project and its volumes are removed at the end; `--keep` leaves them for inspection (log in through `docker compose --project-name libris-restore-test port api 8088`). Compare the counts with production and note the date of the test.

## Restoring production

Only after a data loss or to undo a schema-changing release (see [Rollback](release.md#rollback)). Pause the jobs if the application still runs, then on CT116:

```bash
compose=(docker compose --project-name epub-translator --env-file /opt/epub-translator/.env
  --file /opt/libris-production/docker-compose.yml --profile codex)
backup=/mnt/libris-backup/ct116/libris-<timestamp>      # or a pre-deployment dump, see below
(cd "$backup" && sha256sum --check SHA256SUMS)
"${compose[@]}" stop api worker codex
"${compose[@]}" exec -T database sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error' < "$backup/database.dump"
# Books too, only if they were lost or damaged: this replaces the volume content.
"${compose[@]}" run --rm --no-deps -T api sh -c 'find /data -mindepth 1 -delete && tar -C /data -xzf -' < "$backup/books.tar.gz"
"${compose[@]}" up -d --no-build --wait api codex && "${compose[@]}" up -d --no-build worker
```

For a rollback, use the pre-deployment dump instead (`/opt/libris-production/backups/pre-<timestamp>-<commit>.dump`, the one taken just before the faulty deployment), skip the books, and run `libris-production-deploy --rollback` rather than `up`: it starts the previous images on the restored schema. If `.env` was lost too, restore it first: from `config.env` if the backups include it, otherwise generate a new one with `scripts/setup.py` and enter the provider keys again. Afterwards check `/health`, a login, a book preview, a provider connection test and an EPUB export.
