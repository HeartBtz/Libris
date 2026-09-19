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

# Image shipped with this checkout; scripts/check_version.py keeps it in step with the release.
shipped_image="heartbtz/libris:0.7.0"
official_release='^(docker\.io/)?heartbtz/libris:[0-9]+\.[0-9]+\.[0-9]+$'

# Pick the image to run:
# - LIBRIS_IMAGE in the environment always wins (deliberate upgrade, downgrade or local build);
# - an official release pinned in .env that is older than the shipped one is upgraded, so that
#   `git pull && ./scripts/install-docker.sh` moves to the version of the checkout;
# - anything else written in .env (a newer release, a custom image or tag) is kept.
configured_image="$(env_value LIBRIS_IMAGE)"
if [[ -n "${LIBRIS_IMAGE:-}" ]]; then
	image="$LIBRIS_IMAGE"
elif [[ -z "$configured_image" ]]; then
	image="$shipped_image"
elif [[ "$configured_image" =~ $official_release ]]; then
	configured_version="${configured_image##*:}"
	shipped_version="${shipped_image##*:}"
	newest="$(printf '%s\n%s\n' "$configured_version" "$shipped_version" | sort -V | tail -n 1)"
	if [[ "$newest" == "$configured_version" ]]; then
		image="$configured_image"
	else
		image="$shipped_image"
		echo "Updating LIBRIS_IMAGE in .env: ${configured_image} -> ${image}"
	fi
else
	image="$configured_image"
	echo "Keeping the custom image set in .env: ${image} (set LIBRIS_IMAGE=... to change it)"
fi
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
