# Release process

Libris uses Semantic Versioning. During the `0.x` phase, document any operationally breaking change in `CHANGELOG.md` and the release notes.

## Prepare

1. Update versions in `backend/pyproject.toml`, `backend/app/__init__.py`, `frontend/package.json`, its lockfile, `codex_bridge/package.json`, its lockfile, `codex_bridge/rpc.py` and the `Dockerfile` build argument.
2. Update `CHANGELOG.md` and documentation.
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

## GitHub

Pushing `v*` triggers `.github/workflows/release.yml`. It verifies the tag/version match, runs tests, builds the image, publishes versioned GHCR tags and creates release notes. Repository package permissions and Actions must be enabled.

## GitLab

After the tag pipeline is green, create a release from the same immutable tag in **Deploy > Releases**, using the matching `CHANGELOG.md` section. A source-only release is valid; do not claim a container image unless a registry job actually published it.

## Rollback

Application-only rollback means checking out the prior tag and rebuilding. If a release applied an incompatible database migration, restore the database, `/data` and `.env` from the matching tested backup. Reverting an image does not revert a schema.
