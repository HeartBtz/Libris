#!/usr/bin/env bash
# shellcheck disable=SC2016
set -euo pipefail

mode="${1:---api-only}"
case "$mode" in
--api-only | --worker-when-idle | --force-worker) ;;
*)
	echo "Usage: $0 [--api-only|--worker-when-idle|--force-worker]" >&2
	exit 2
	;;
esac


compose=(docker compose)
if [[ -n "${LIBRIS_COMPOSE_ENV_FILE:-}" ]]; then
	compose+=(--env-file "$LIBRIS_COMPOSE_ENV_FILE")
fi

api_container="$("${compose[@]}" ps -q api)"
previous_image=""
worker_changed=false
if [[ -n "$api_container" ]]; then
	previous_image="$(docker inspect --format '{{.Image}}' "$api_container")"
fi

rollback() {
	status=$?
	trap - ERR
	if [[ -n "$previous_image" ]]; then
		echo "Deployment failed; restoring the previous application image." >&2
		docker image tag "$previous_image" epub-translator:rollback
		export LIBRIS_IMAGE=epub-translator:rollback
		services=(api)
		[[ "$worker_changed" == true ]] && services+=(worker)
		"${compose[@]}" --profile codex up -d --no-build --wait "${services[@]}" || true
	fi
	exit "$status"
}
trap rollback ERR

case "${LIBRIS_DEPLOY_SOURCE:-build}" in
build) "${compose[@]}" --profile codex build api ;;
pull) "${compose[@]}" --profile codex pull api ;;
loaded) docker image inspect "${LIBRIS_IMAGE:?LIBRIS_IMAGE is required for a loaded deployment}" >/dev/null ;;
*)
	echo "Invalid LIBRIS_DEPLOY_SOURCE" >&2
	exit 2
	;;
esac

database_revision() {
	"${compose[@]}" exec -T database sh -lc \
		'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version ORDER BY version_num;"' \
		2>/dev/null | tr -d '\r' | sort | tr '\n' ' ' || true
}
target_revision() {
	"${compose[@]}" run --rm --no-deps -T migrate alembic heads | sed -n 's/ .*//p' | sort | tr '\n' ' '
}
active_jobs() {
	"${compose[@]}" exec -T database sh -lc \
		'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM jobs WHERE status IN ('"'"'pending'"'"','"'"'waiting'"'"','"'"'analyzing'"'"','"'"'translating'"'"','"'"'reviewing'"'"','"'"'syncing'"'"');"'
}
migrate() {
	"${compose[@]}" --profile codex up --no-build --wait --force-recreate migrate
}

# The migration never runs under a worker of the previous version: that worker would keep writing
# with the old schema in mind. --api-only refuses when a migration is pending and the worker runs;
# the other modes stop the application and the worker first.
if [[ "$mode" == "--api-only" ]]; then
	target="$(target_revision)"
	if [[ -n "$("${compose[@]}" ps -q --status running worker)" && "$(database_revision)" != "$target" ]]; then
		echo "A database migration is pending (target ${target% }) and the worker is running: --api-only" >&2
		echo "would migrate under the previous worker. Nothing was changed; use --worker-when-idle or" >&2
		echo "--force-worker." >&2
		trap - ERR
		exit 5
	fi
	migrate
	"${compose[@]}" --profile codex up -d --no-build --wait api
	trap - ERR
	echo "API updated; the existing worker was left untouched."
	exit 0
fi

if [[ "$mode" == "--worker-when-idle" ]]; then
	for _ in $(seq 1 120); do
		active="$(active_jobs)"
		[[ "$active" == "0" ]] && break
		sleep 5
	done
	if [[ "${active:-1}" != "0" ]]; then
		trap - ERR
		echo "Worker not restarted: jobs remained active for 10 minutes. Nothing was changed." >&2
		exit 3
	fi
	# Stop submissions before the final check so the worker cannot claim a new job in the gap.
	"${compose[@]}" stop api
	active="$(active_jobs)"
	if [[ "$active" != "0" ]]; then
		"${compose[@]}" start api
		trap - ERR
		echo "Worker not restarted: a job became active at the drain boundary. Nothing was changed." >&2
		exit 4
	fi
fi

# --worker-when-idle (queue drained) and --force-worker: stop both, migrate, start the new version.
worker_changed=true
"${compose[@]}" stop api worker
migrate
"${compose[@]}" --profile codex up -d --no-build --wait api worker
trap - ERR
echo "Worker updated. Persistent checkpoints remain the source of truth."
