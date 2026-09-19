"""Container and supply-chain hardening (audit S-8 / I-30): checked in the files that ship it."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def service(name: str) -> str:
    match = re.search(rf"^  {name}:\n((?:    .*\n|\n)+)", COMPOSE, re.M)
    assert match, f"service {name} not found"
    return match.group(1)


def anchor(name: str) -> str:
    match = re.search(rf"^x-{name}: &{name}\n((?:  .*\n)+)", COMPOSE, re.M)
    assert match, f"x-{name} not found"
    return match.group(1)


def test_every_service_drops_capabilities_and_privilege_escalation():
    app = anchor("app")
    blocks = {"app": app, "codex": service("codex"), "database": service("database")}
    for name, block in blocks.items():
        assert "no-new-privileges:true" in block, name
        assert "cap_drop: [ALL]" in block, name
        assert re.search(r"read_only: true", block), name
    for name in ("migrate", "api", "worker"):
        assert "<<: *app" in service(name), name
    # The database keeps only what its entrypoint needs to own its data and drop to `postgres`.
    assert "cap_add: [CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID]" in service("database")


def test_read_only_services_have_their_writable_places():
    app = anchor("app")
    assert "TMPDIR: /data/tmp" in app and "- books:/data" in app and "- /tmp:" in app
    database = service("database")
    assert "- /var/run/postgresql" in database and "- database:/var/lib/postgresql/data" in database


def test_images_install_only_hashed_dependencies():
    assert (
        "pip install --no-cache-dir --require-hashes -r requirements.lock"
        in (ROOT / "Dockerfile").read_text()
    )
    assert "--require-hashes -r /tmp/requirements.lock" in (ROOT / "codex_bridge" / "Dockerfile").read_text()
    locks = [ROOT / "backend" / "requirements.lock", ROOT / "codex_bridge" / "requirements.lock"]
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "hash_lock.py"), "--check", *map(str, locks)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_compose_file_travels_with_the_image():
    assert "COPY docker-compose.yml /app/deploy/docker-compose.yml" in (ROOT / "Dockerfile").read_text()


def test_a_pin_without_hash_is_reported(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "alpha==1.0 \\\n    --hash=sha256:" + "0" * 64 + "\n    # via x\nbeta==2.0\n    # via y\n"
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "hash_lock.py"), "--check", str(lock)],
        capture_output=True,
        text=True,
    )
    assert (
        result.returncode != 0 and "no hash for beta==2.0" in result.stderr and "alpha" not in result.stderr
    )
