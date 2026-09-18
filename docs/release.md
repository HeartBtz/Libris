# Release process

Libris uses Semantic Versioning. During the `0.x` phase, document any operationally breaking change in `CHANGELOG.md` and the release notes.

## Prepare

1. Update versions in `backend/pyproject.toml`, `backend/app/__init__.py`, `frontend/package.json`, its lockfile, `codex_bridge/package.json`, its lockfile, `codex_bridge/rpc.py`, `.env.example`, `scripts/install-docker.sh`, both Dockerfile build arguments, and the pinned `heartbtz/libris:X.Y.Z` references in `README.md`, `docs/docker.md` and `docs/docker-hub.md` (checked by `scripts/check_version.py`).
2. Update `CHANGELOG.md` and documentation. Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`: that section alone becomes the GitLab and GitHub release notes (check with `python3 scripts/release_notes.py vX.Y.Z`).
3. Run `python3 scripts/check_version.py`.
4. Run backend tests, Ruff, frontend build, migration tests, dependency audits, Gitleaks and the disposable installation check.
5. Build the main and optional Codex images without cache.
6. Confirm the branch is clean, pushed and protected CI is green.

## Tag

```bash
VERSION=0.1.0
python3 scripts/check_version.py "v${VERSION}"
git tag -s "v${VERSION}" -m "Libris ${VERSION}"
git push origin "v${VERSION}"
```

Use an annotated unsigned tag only when commit signing is not configured, and disclose that limitation in release notes. Never move an existing release tag.

## Canonical GitLab release

Push the release commit to the canonical GitLab repository. Its protected default-branch pipeline builds the commit-SHA images, checks them and, once every job has passed, marks them `verified-sha-<commit>`. The tag pipeline does not run the tests again: it verifies that the tag points into the default branch, waits (up to 20 minutes) for that marker, reuses the verified images by digest, promotes them to GitLab Container Registry and Docker Hub tags, then creates the GitLab release. Docker Hub publication requires protected masked `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` variables.

## GitHub mirror

GitLab push-mirrors the same immutable tag to GitHub. The mirrored `v*` tag triggers `.github/workflows/release.yml`, which verifies the tag/version match, publishes versioned GHCR tags with provenance and SBOM metadata, and creates the GitHub release. Never push a divergent release commit directly to the mirror.

Verify each destination independently. A green source pipeline does not prove that the mirror, registry or downstream workflow completed. See [CI/CD and public mirrors](ci-cd.md).

## Production

The protected `deploy-production` job then runs automatically, and only after the image runtime test, the vulnerability scan and the GitLab publication have succeeded: a failed gate leaves production untouched. It deploys only to the existing CT116-OpenCode production stack, creates a PostgreSQL backup, restarts the API and worker from the tested commit image and verifies `/health`. Confirm the reported version and worker checkpoint recovery directly on CT116 before announcing completion.

## Rollback

A release that migrated fine but misbehaves can be rolled back from its tag pipeline: run the manual, protected `rollback-production` job (serialized with `deploy-production` by the same `resource_group`). On CT116 it calls `libris-production-deploy --rollback`, which redeploys the images that this deployment replaced — retained on the host as `libris-production:previous-api` and `libris-codex-production:previous` — through the same guarded procedure: pre-deployment dump, stop, `migrate` (a no-op), start, `/health` on the older version, worker restart from its checkpoints. It only goes backwards (the retained version must be older than the deployed one), so running it twice does not re-deploy the faulty release.

It refuses, before touching anything, when the database schema is no longer the one the older version expects, that is when the release added a migration. Reverting an image does not revert a schema; in that case restore the matching pre-deployment dump instead:

1. Stop the application services: `docker compose --project-name epub-translator --env-file /opt/epub-translator/.env --file /opt/libris-production/docker-compose.yml --profile codex stop api worker codex`.
2. Restore `/opt/libris-production/backups/pre-<timestamp>-<commit>.dump` (the newest one, taken just before the faulty deployment) with `pg_restore --clean --if-exists --no-owner` inside the `database` container, as described in [backup and restore](backup.md).
3. Run `libris-production-deploy --rollback` again: the schema now matches and the previous images are redeployed.

Books (`/data`) and `.env` are not changed by a deployment; restore them from the scheduled backup only if they were damaged. After a rollback, the next release is deployed normally by its tag.

The procedure removes old Libris images after each successful deployment or rollback: it keeps the deployed images, the retained `previous-*` ones and any image a container uses (so at most two versions), and deletes the other `libris-production:*` / `libris-codex-production:*` tags and the untagged pulls of `192.168.1.126:5050/dev/libris`; no other image and no volume is touched. `libris-production-deploy --prune-images --dry-run` lists what would be removed, `--prune-images` removes it.
