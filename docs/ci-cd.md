# GitLab CI/CD, CT116 production and public mirrors

GitLab at `git.hbtz.fr/HeartBtz/libris` is the canonical repository and release pipeline. The single production target is the current Libris installation on CT116-OpenCode. GitHub at `github.com/HeartBtz/Libris` remains a read-only public push mirror for discovery, issues and GitHub-native releases.

## Pipeline flow

All jobs run on the CT105 shell runner and start their tools with `docker run`; containers, networks and Compose projects are named after `$CI_JOB_ID` so that up to eight concurrent jobs never collide.

| Pipeline | Jobs |
| --- | --- |
| Merge request, branch | `backend` (Ruff + pytest on SQLite), `backend-postgres` (migration round trip + pytest on PostgreSQL), `frontend` (build, `npm audit`, Playwright specs that mock the API), `audit` (version consistency, `pip-audit`, Gitleaks) |
| Default branch | the same, then `container-build`, `container-runtime`, `container-scan`, and `verified-image` once everything passed |
| Release tag `vX.Y.Z` | `release-policy`, `release-images`, `container-runtime`, `container-scan`, publication, release, `deploy-production` |

A merge request that only changes documentation (nothing under `backend/`, `frontend/`, `codex_bridge/`, `prompts/`, `scripts/`, `deploy/`, the Dockerfile, the Compose files or this pipeline) only runs `audit`: version pins live in the README and docs. pip and npm downloads are cached per lockfile (`.cache/pip`, `.cache/npm` in the runner cache).

Only the protected default branch builds and pushes the commit-addressed application and Codex images (`sha-<commit>`, `codex-sha-<commit>`). The build job records their registry digests as a dotenv artifact; runtime checks, the packaged EPUBCheck smoke test, the HIGH/CRITICAL vulnerability scan, publication and deployment all consume those exact digest references. Trivy analyses each image once: the application report is both the gate and the CycloneDX SBOM artifact (every package, HIGH/CRITICAL findings that have a fix).

When every job of the default-branch pipeline has passed, `verified-image` adds the `verified-sha-<commit>` tag. A release tag must point to a commit contained in the default branch and does not run the tests again: `release-images` waits up to 20 minutes for that marker (the tag is often pushed while the branch pipeline is still running), then promotes the `sha-<commit>` digests. If the branch pipeline failed, make it pass and retry `release-images`.

A semantic tag such as `v0.3.1` promotes that exact SHA image to three version aliases:

| Destination | Tags | Owner |
| --- | --- | --- |
| GitLab Container Registry | `0.3.1`, `0.3`, `latest` | GitLab pipeline |
| Docker Hub `heartbtz/libris` | `0.3.1`, `0.3`, `latest` | GitLab pipeline |
| GHCR `ghcr.io/heartbtz/libris` | semantic and SHA tags | Mirrored tag and GitHub Actions |

GitLab also creates its release object from the protected tag. The GitHub mirror receives branches and tags; its release workflow verifies the same version, rebuilds independently, publishes GHCR provenance/SBOM metadata and creates the GitHub release.

## Required GitLab settings

Protect `main` and tags matching `v*`. Enable the project Container Registry, protect immutable `sha-*`, `codex-sha-*`, `verified-sha-*` and exact-version image tags from overwrites, and keep the existing GitHub push mirror directed from GitLab to GitHub. Enable **Prevent outdated deployment jobs** and disable retries of outdated deployment jobs. Do not push release commits directly to GitHub because the next mirror update can overwrite divergent refs.

Add these protected and masked CI/CD variables in **Settings > CI/CD > Variables**:

| Variable | Value |
| --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub account that owns `heartbtz/libris` |
| `DOCKERHUB_TOKEN` | Docker Hub access token with read/write permission for that repository |

The predefined `CI_REGISTRY`, `CI_REGISTRY_IMAGE`, `CI_REGISTRY_USER` and `CI_REGISTRY_PASSWORD` variables authenticate the GitLab registry. Never store a Docker password or token in the repository.

After pushing release tags, the same protected job updates the Docker Hub Overview from `docs/docker-hub.md` and verifies that Docker Hub retained the complete text.

Use the untagged `CT105-Dev-CI` group runner for protected semver-tag deployment, as for Relay. CT105 reaches CT116 through its `client-gitlab` Teleport Machine ID; it must never use a LAN address, a copied SSH key, or an SSH deployment variable. CT116 stores a root-only GitLab deploy token with the single `read_registry` scope and the registry trust anchor.

If Docker Hub variables are absent, the Docker Hub publication job is omitted rather than exposing or guessing credentials. GitLab Registry and release creation still proceed.

## Release procedure

1. Update all version fields and `CHANGELOG.md`.
2. Run the checks in [release process](release.md).
3. Commit and push `main` to canonical GitLab.
4. Wait for the branch pipeline to pass.
5. Create and push the immutable annotated `vX.Y.Z` tag.
6. Verify GitLab Registry, Docker Hub, the GitLab release, the GitHub mirror, GHCR and the GitHub release independently.
7. Let the protected `deploy-production` job run: it starts automatically once the image runtime test, the vulnerability scan and the GitLab publication have succeeded. Verify CT116 independently.

Do not treat a green pipeline as deployment proof. Pull each published digest, inspect its OCI version/revision labels and query `/health` from a disposable container stack before announcing the release.

Scheduled Dependabot version pull requests are disabled on the read-only GitHub mirror because merging them there would diverge from canonical GitLab. Dependency audits remain in both CI systems; planned upgrades must be prepared and tested on GitLab, while GitHub security alerts remain useful as advisory input.

## CT116 production deployment

The protected semver-tag `deploy-production` job is serialized by `resource_group` and runs automatically on CT105. It opens an audited Teleport session to CT116, where the root-owned target pulls the digest-pinned application and Codex images from the GitLab registry using its local read-only identity. The target then passes their commit, version and immutable image IDs to the preinstalled root-owned deployment procedure. That fixed procedure creates a transactionally consistent PostgreSQL dump while the current application remains available, then gracefully stops the old API, worker and Codex bridge for the migration and image switch. It preserves the existing named volumes and private `/opt/epub-translator/.env`, starts the API and Codex bridge, verifies the exact version through `/health`, and only then restarts the worker from its checkpoints. A healthy redeploy of the same commit is a no-op. The procedure also rejects an older version or a reused version number associated with another commit.

The procedure intentionally restarts the worker with checkpoint recovery. If health fails and the schema did not change, it verifies restoration of the previous images. After a schema change it leaves application services stopped and retains the pre-deployment dump rather than attempting an unsafe automatic downgrade. Keep the dedicated runner, fixed Compose file, deployment procedure and protected production environment provisioned outside Git. Never put `.env`, registry credentials or user books in this repository.

### What lives outside Git on the production target

| Path on CT116 | Purpose | Recreated by |
|---|---|---|
| `/usr/local/sbin/libris-production-deploy` | fixed deployment procedure (copy of `deploy/libris-production-deploy`) | operator |
| `/opt/libris-production/docker-compose.yml` | the Compose file the procedure drives; it is the repository `docker-compose.yml`, unmodified | operator |
| `/opt/libris-production/current-*` | deployed version, commit and image IDs | the procedure, after each successful deployment |
| `/opt/libris-production/backups/pre-*.dump` | the five most recent pre-deployment PostgreSQL dumps (several GB each) | the procedure |
| `/opt/epub-translator/.env` | secrets; never stored anywhere else | operator |
| `/etc/libris-registry/config.json` | read-only registry credentials | operator |

`/opt/libris-production` holds multi-gigabyte dumps: when disk space is short, delete old files inside `backups/`, never the directory itself. Without `docker-compose.yml` the next `deploy-production` job stops with `Production configuration is not provisioned` (exit 65) before touching anything; the running containers are unaffected.

### Re-provisioning the target directory

If `/opt/libris-production` is lost, recreate it from the tag that is currently deployed, then verify before the next release:

```bash
install -d -m 0700 /opt/libris-production
git -C /opt/epub-translator show "v$(curl -fsS http://192.168.1.116:8088/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'):docker-compose.yml" \
  | install -m 0600 /dev/stdin /opt/libris-production/docker-compose.yml
api="$(docker inspect --format '{{.Config.Image}}' epub-translator-api-1)"
codex="$(docker inspect --format '{{.Config.Image}}' epub-translator-codex-1)"
LIBRIS_IMAGE="$api" LIBRIS_CODEX_IMAGE="$codex" LIBRIS_ENV_FILE=/opt/epub-translator/.env \
  docker compose --project-name epub-translator --env-file /opt/epub-translator/.env \
  --file /opt/libris-production/docker-compose.yml --profile codex config --services
```

The last command must list the five services (`database`, `migrate`, `api`, `worker`, `codex`) without error; `git -C /opt/epub-translator fetch --tags` first if the deployed tag is unknown to that checkout. The `current-*` markers are optional: the procedure recreates them, and only uses them to refuse an older version or a version number reused by another commit. Compose may report a different `config-hash` than the running containers even when the effective configuration is identical; the next deployment recreates the application containers anyway. A lost `backups/` directory means no pre-deployment dump exists until the next deployment: take a manual `pg_dump` first if a schema migration is pending.
