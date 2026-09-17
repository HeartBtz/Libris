#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
profile=()

if (($#)); then
	if [[ $# -ne 2 || "$1" != "--profile" || "$2" != "codex" ]]; then
		echo "Usage: $0 [--profile codex]" >&2
		exit 2
	fi
	profile=(--profile codex)
fi

if ! command -v docker >/dev/null 2>&1; then
	echo "Docker is required: https://docs.docker.com/engine/install/" >&2
	exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
	echo "Docker Compose v2 is required." >&2
	exit 1
fi

if [[ ! -f "$root/.env" ]]; then
	if command -v python3 >/dev/null 2>&1; then
		python3 "$root/scripts/setup.py"
	else
		docker run --rm --user "$(id -u):$(id -g)" \
			--volume "$root:/work" --workdir /work python:3.13-slim \
			python scripts/setup.py
	fi
fi

env_value() {
	local name="$1"
	grep -m 1 "^${name}=" "$root/.env" | cut -d= -f2- || true
}

configured_image="$(env_value LIBRIS_IMAGE)"
image="${LIBRIS_IMAGE:-${configured_image:-heartbtz/libris:0.4.0}}"
if grep -q '^LIBRIS_IMAGE=' "$root/.env"; then
	sed -i "s|^LIBRIS_IMAGE=.*|LIBRIS_IMAGE=${image}|" "$root/.env"
else
	printf '\nLIBRIS_IMAGE=%s\n' "$image" >>"$root/.env"
fi
chmod 600 "$root/.env"

export LIBRIS_IMAGE="$image"
compose=(docker compose --project-directory "$root" "${profile[@]}")
services=(database migrate api worker)
"${compose[@]}" pull "${services[@]}"
if ((${#profile[@]})); then
	"${compose[@]}" build codex
	services+=(codex)
fi
"${compose[@]}" up -d --no-build --wait "${services[@]}"

bind_address="${BIND_ADDRESS:-$(env_value BIND_ADDRESS)}"
port="${PORT:-$(env_value PORT)}"
bind_address="${bind_address:-127.0.0.1}"
port="${port:-8088}"
if [[ "$bind_address" == "0.0.0.0" || "$bind_address" == "::" ]]; then
	bind_address="localhost"
fi
echo "Libris is ready on http://${bind_address}:${port}"
echo "The initial username and password are stored in $root/.env."
