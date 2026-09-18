"""deploy/libris-backup and deploy/libris-restore (dry run) against a fake `docker`."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

# Plays the database container (pg_dump), the books volume (a local directory archived with the real
# tar) and the read-back checks. A dump is "complete" when it ends with the marker pg_dump writes here.
FAKE_DOCKER = r'''#!PYTHON
import os, subprocess, sys
args = sys.argv[1:]
log = open(os.environ["FAKE_DOCKER_LOG"], "a")
log.write(" ".join(args) + "\n")
if args[0] == "ps":
    print("database-container")
elif args[:2] == ["volume", "inspect"]:
    sys.exit(0 if args[2] == "epub-translator_books" else 1)
elif args[0] == "inspect":
    print("postgres:17-bookworm")
elif args[0] == "exec":
    if os.environ.get("FAKE_PG_DUMP_FAILS"):
        sys.exit(1)
    sys.stdout.buffer.write(b"PGDMP" + b"x" * 1000 + b"END")
elif args[0] == "run" and "tar" in args and "-czf" in args:
    subprocess.run(["tar", "-czf", "-", "-C", os.environ["FAKE_BOOKS"], "."], check=True)
elif args[0] == "run" and "pg_restore" in args:
    data = sys.stdin.buffer.read()
    sys.exit(0 if data.startswith(b"PGDMP") and data.endswith(b"END") else 1)
elif args[0] == "run" and "-tzf" in args:
    sys.exit(subprocess.run(["tar", "-tzf", "-"], stdin=sys.stdin).returncode)
else:
    sys.exit(f"unexpected docker call: {args}")
'''

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("bash", "tar", "sha256sum")), reason="bash, tar and sha256sum are required"
)


@pytest.fixture
def host(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER.replace("#!PYTHON", "#!" + sys.executable))
    fake.chmod(0o755)
    books = tmp_path / "books-volume"
    (books / "books").mkdir(parents=True)
    (books / "books" / "one.epub").write_bytes(b"epub")
    destination = tmp_path / "backups"
    destination.mkdir()
    secret = tmp_path / "secret.env"
    secret.write_text("SECRET_KEY=x\nPOSTGRES_PASSWORD=y\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
        "FAKE_BOOKS": str(books),
        "LIBRIS_BACKUP_DESTINATION": str(destination),
        "LIBRIS_BACKUP_SECRET_ENV": str(secret),
        "LIBRIS_PRODUCTION_BASE": str(tmp_path / "production"),
    }
    return env, destination


def backup(env):
    return subprocess.run(["bash", str(DEPLOY / "libris-backup")], env=env, capture_output=True, text=True, timeout=60)


def test_a_backup_holds_a_verified_dump_books_and_configuration(host):
    env, destination = host
    result = backup({**env, "LIBRIS_BACKUP_INCLUDE_ENV": "true"})
    assert result.returncode == 0, result.stderr
    (made,) = destination.iterdir()
    assert made.name.startswith("libris-") and made.name.endswith("Z")
    assert {p.name for p in made.iterdir()} == {"database.dump", "books.tar.gz", "config.env", "backup.info", "SHA256SUMS"}
    listing = subprocess.run(["tar", "-tzf", str(made / "books.tar.gz")], capture_output=True, text=True).stdout
    assert "./books/one.epub" in listing
    assert subprocess.run(["sha256sum", "--check", "--quiet", "SHA256SUMS"], cwd=made).returncode == 0
    assert oct(made.joinpath("config.env").stat().st_mode & 0o777) == "0o600"


def test_a_failed_dump_fails_the_job_and_leaves_no_partial_backup(host):
    env, destination = host
    result = backup({**env, "FAKE_PG_DUMP_FAILS": "1"})
    assert result.returncode != 0 and "pg_dump failed" in result.stderr
    assert list(destination.iterdir()) == []


def test_an_unmounted_share_is_refused(host, tmp_path):
    env, destination = host
    result = backup({**env, "LIBRIS_BACKUP_MOUNTPOINT": str(tmp_path)})
    assert result.returncode != 0 and "is not mounted" in result.stderr
    assert list(destination.iterdir()) == []


def test_rotation_removes_only_expired_backups(host):
    env, destination = host
    expired, recent, unrelated = (destination / name for name in ("libris-20200101T000000Z", "libris-recent-Z", "other"))
    for directory in (expired, recent, unrelated):
        directory.mkdir()
    old = time.time() - 15 * 86400
    os.utime(expired, (old, old))
    os.utime(unrelated, (old, old))
    result = backup({**env, "LIBRIS_BACKUP_RETENTION_DAYS": "14"})
    assert result.returncode == 0, result.stderr
    assert not expired.exists() and recent.exists() and unrelated.exists()
    assert f"Removed expired backup {expired}" in result.stdout


def restore(env, *arguments):
    return subprocess.run(
        ["bash", str(DEPLOY / "libris-restore"), *arguments], env=env, capture_output=True, text=True, timeout=60
    )


def test_the_restore_dry_run_checks_the_backup_and_prints_an_isolated_plan(host, tmp_path):
    env, destination = host
    assert backup(env).returncode == 0
    (made,) = destination.iterdir()
    assert not (made / "config.env").exists()  # secrets stay on the host unless asked
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n")
    env = {
        **env,
        "LIBRIS_RESTORE_COMPOSE_FILE": str(compose),
        "LIBRIS_RESTORE_IMAGE": "sha256:app",
        "LIBRIS_PRODUCTION_SECRET_ENV": env["LIBRIS_BACKUP_SECRET_ENV"],
    }
    calls = (tmp_path / "docker.log").read_text()
    result = restore(env, "--dry-run", str(made))
    assert result.returncode == 0, result.stderr
    plan = result.stdout
    assert "checksums and books archive verified" in plan
    assert "--project-name libris-restore-test" in plan and "--project-name epub-translator" not in plan
    assert "pg_restore" in plan and "tar -C /data -xzf -" in plan and "alembic check" in plan
    assert f"--env-file {env['LIBRIS_BACKUP_SECRET_ENV']}" in plan and " worker" not in plan
    assert (tmp_path / "docker.log").read_text() == calls  # a dry run calls no Docker command

    (made / "books.tar.gz").write_bytes(b"damaged")
    damaged = restore(env, "--dry-run", str(made))
    assert damaged.returncode != 0 and "checksums do not match" in damaged.stderr


def test_the_restore_refuses_the_production_project(host, tmp_path):
    env, destination = host
    assert backup(env).returncode == 0
    (made,) = destination.iterdir()
    result = restore({**env, "LIBRIS_RESTORE_PROJECT": "epub-translator"}, "--dry-run", str(made))
    assert result.returncode != 0 and "production project" in result.stderr
