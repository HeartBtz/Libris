#!/usr/bin/env bash
set -euo pipefail

mode="${1:---api-only}"
case "$mode" in
--api-only | --worker-when-idle | --force-worker) ;;
*)
	echo "Usage: $0 [--api-only|--worker-when-idle|--force-worker]" >&2
	exit 2
	;;
esac

api_container="$(docker compose ps -q api)"
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
		docker image tag "$previous_image" epub-translator:local
		services=(api)
		[[ "$worker_changed" == true ]] && services+=(worker)
		docker compose --profile codex up -d --no-build --wait "${services[@]}" || true
	fi
	exit "$status"
}
trap rollback ERR

docker compose --profile codex build api
docker compose --profile codex up --no-build --wait --force-recreate migrate

if [[ "$mode" == "--api-only" ]]; then
	docker compose --profile codex up -d --no-build --wait api
	echo "API updated; the existing worker was left untouched."
	exit 0
fi

if [[ "$mode" == "--worker-when-idle" ]]; then
	for _ in $(seq 1 120); do
		active="$(docker compose exec -T database sh -lc \
			'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM jobs WHERE status IN ('"'"'pending'"'"','"'"'waiting'"'"','"'"'analyzing'"'"','"'"'translating'"'"','"'"'reviewing'"'"','"'"'syncing'"'"');"')"
		[[ "$active" == "0" ]] && break
		sleep 5
	done
	if [[ "${active:-1}" != "0" ]]; then
		[[ -n "$previous_image" ]] && docker image tag "$previous_image" epub-translator:local
		echo "Worker not restarted: jobs remained active for 10 minutes." >&2
		exit 3
	fi
	# Stop submissions before the final check so the worker cannot claim a new job in the gap.
	docker compose stop api
	active="$(docker compose exec -T database sh -lc \
		'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM jobs WHERE status IN ('"'"'pending'"'"','"'"'waiting'"'"','"'"'analyzing'"'"','"'"'translating'"'"','"'"'reviewing'"'"','"'"'syncing'"'"');"')"
	if [[ "$active" != "0" ]]; then
		docker compose start api
		[[ -n "$previous_image" ]] && docker image tag "$previous_image" epub-translator:local
		echo "Worker not restarted: a job became active at the drain boundary." >&2
		exit 4
	fi
	worker_changed=true
	docker compose stop worker
fi

[[ "$mode" == "--force-worker" ]] && worker_changed=true
docker compose --profile codex up -d --no-build --wait api worker
trap - ERR
echo "Worker updated. Persistent checkpoints remain the source of truth."
