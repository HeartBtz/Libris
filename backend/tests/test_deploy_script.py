"""scripts/deploy.sh driven against a fake `docker`: the migration never runs under the previous worker."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"

# Records every call and answers the few the script reads: running containers, the database revision,
# the image's migration head and the number of active jobs.
FAKE_DOCKER = r"""#!PYTHON
import json, os, sys
path = os.environ["FAKE_DOCKER_STATE"]
state = json.load(open(path))
args = sys.argv[1:]
state["calls"].append(args)
json.dump(state, open(path, "w"))
output = ""
if args[0] == "compose" and "ps" in args:
    service = args[-1]
    output = state["running"].get(service, "")
elif args[0] == "compose" and "exec" in args and "alembic_version" in args[-1]:
    output = state["database_revision"]
elif args[0] == "compose" and "exec" in args and "jobs" in args[-1]:
    output = str(state["active_jobs"])
elif args[0] == "compose" and "run" in args and "heads" in args:
    output = state["head"] + " (head)"
elif args[0] == "inspect":
    output = "sha256:previous"
if output:
    print(output)
"""


def setup(tmp_path, *, database_revision="new", worker_running=True, active_jobs=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER.replace("#!PYTHON", "#!" + sys.executable))
    fake.chmod(0o755)
    state = tmp_path / "docker.json"
    running = {"api": "c-api", "worker": "c-worker" if worker_running else ""}
    state.write_text(
        json.dumps(
            {
                "calls": [],
                "running": running,
                "database_revision": database_revision,
                "head": "new",
                "active_jobs": active_jobs,
            }
        )
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(state),
        "LIBRIS_DEPLOY_SOURCE": "pull",
    }
    return state, env


def run(env, *arguments):
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments], env=env, capture_output=True, text=True, timeout=60
    )


def compose_calls(state: Path) -> list[list[str]]:
    calls = json.loads(state.read_text())["calls"]
    return [call[call.index("compose") + 1 :] for call in calls if call[0] == "compose"]


def index_of(calls, *words):
    return next(i for i, call in enumerate(calls) if all(word in call for word in words))


def migrated(calls) -> bool:
    return any("up" in call and "migrate" in call for call in calls)


pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")


def test_api_only_refuses_a_pending_migration_while_the_worker_runs(tmp_path):
    state, env = setup(tmp_path, database_revision="old")
    result = run(env, "--api-only")
    assert result.returncode == 5, result.stderr
    assert "migration is pending" in result.stderr
    calls = compose_calls(state)
    assert not migrated(calls)
    assert not any("up" in call and "api" in call for call in calls)


def test_api_only_updates_the_api_when_the_schema_is_current(tmp_path):
    state, env = setup(tmp_path)
    result = run(env, "--api-only")
    assert result.returncode == 0, result.stderr
    calls = compose_calls(state)
    assert index_of(calls, "up", "migrate") < index_of(calls, "up", "-d", "api")
    assert not any("worker" in call and ("stop" in call or "up" in call) for call in calls)


def test_api_only_migrates_when_no_worker_runs(tmp_path):
    state, env = setup(tmp_path, database_revision="", worker_running=False)
    assert run(env, "--api-only").returncode == 0
    assert migrated(compose_calls(state))


def test_force_worker_stops_the_application_before_migrating(tmp_path):
    state, env = setup(tmp_path, database_revision="old")
    result = run(env, "--force-worker")
    assert result.returncode == 0, result.stderr
    calls = compose_calls(state)
    stop = index_of(calls, "stop", "api", "worker")
    assert stop < index_of(calls, "up", "migrate") < index_of(calls, "up", "-d", "api", "worker")


def test_worker_when_idle_migrates_only_after_the_queue_is_drained(tmp_path):
    state, env = setup(tmp_path, database_revision="old")
    result = run(env, "--worker-when-idle")
    assert result.returncode == 0, result.stderr
    calls = compose_calls(state)
    assert index_of(calls, "stop", "api", "worker") < index_of(calls, "up", "migrate")


def test_worker_when_idle_changes_nothing_while_jobs_stay_active(tmp_path):
    state, env = setup(tmp_path, database_revision="old", active_jobs=2)
    fake_sleep = tmp_path / "bin" / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o755)
    result = run(env, "--worker-when-idle")
    assert result.returncode == 3
    calls = compose_calls(state)
    assert not migrated(calls)
    assert not any("stop" in call for call in calls)
