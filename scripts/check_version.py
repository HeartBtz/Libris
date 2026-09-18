"""Verify that release metadata uses one version across the repository."""

import json
import re
import sys
from pathlib import Path

import tomllib

root = Path(__file__).resolve().parents[1]
backend = tomllib.loads((root / "backend/pyproject.toml").read_text())["project"][
    "version"
]
frontend = json.loads((root / "frontend/package.json").read_text())["version"]
frontend_lock_data = json.loads((root / "frontend/package-lock.json").read_text())
frontend_lock = frontend_lock_data["version"]
frontend_lock_root = frontend_lock_data["packages"][""]["version"]
bridge = json.loads((root / "codex_bridge/package.json").read_text())["version"]
bridge_lock_data = json.loads((root / "codex_bridge/package-lock.json").read_text())
bridge_lock = bridge_lock_data["version"]
bridge_lock_root = bridge_lock_data["packages"][""]["version"]
app_source = (root / "backend/app/__init__.py").read_text()
app = re.search(r'__version__ = "([^"]+)"', app_source).group(1)
docker_source = (root / "Dockerfile").read_text()
docker = re.search(r"^ARG LIBRIS_VERSION=([^\s]+)$", docker_source, re.MULTILINE).group(
    1
)
codex_docker_source = (root / "codex_bridge/Dockerfile").read_text()
codex_docker = re.search(
    r"^ARG LIBRIS_VERSION=([^\s]+)$", codex_docker_source, re.MULTILINE
).group(1)
env_example = (root / ".env.example").read_text()
docker_hub = re.search(
    r"^LIBRIS_IMAGE=[^:]+:([^\s]+)$", env_example, re.MULTILINE
).group(1)
installer_source = (root / "scripts/install-docker.sh").read_text()
installer = re.search(r"heartbtz/libris:([^}\s]+)", installer_source).group(1)
rpc_source = (root / "codex_bridge/rpc.py").read_text()
rpc = re.search(
    r'"clientInfo": \{"name": "libris", "version": "([^"]+)"\}', rpc_source
).group(1)
versions = {
    "backend": backend,
    "frontend": frontend,
    "frontend lock": frontend_lock,
    "frontend lock root": frontend_lock_root,
    "bridge": bridge,
    "bridge lock": bridge_lock,
    "bridge lock root": bridge_lock_root,
    "app": app,
    "Docker image": docker,
    "Codex Docker image": codex_docker,
    "Docker Hub image": docker_hub,
    "Docker installer": installer,
    "Codex RPC": rpc,
}
for document in ("README.md", "docs/docker.md", "docs/docker-hub.md"):
    # Installation guides pin an exact image: a stale pin silently installs an old release.
    for number, pinned in enumerate(
        re.findall(r"heartbtz/libris:(\d+\.\d+\.\d+)", (root / document).read_text())
    ):
        versions[f"{document} pin {number + 1}"] = pinned
if len(set(versions.values())) != 1:
    raise SystemExit(f"Version mismatch: {versions}")
if len(sys.argv) == 2 and sys.argv[1].removeprefix("v") != backend:
    raise SystemExit(f"Tag {sys.argv[1]} does not match version {backend}")
print(f"Libris version {backend} is consistent.")
