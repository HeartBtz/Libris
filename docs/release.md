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

Application-only rollback means checking out the prior tag and rebuilding. If a release applied an incompatible database migration, restore the database, `/data` and `.env` from the matching tested backup. Reverting an image does not revert a schema.
