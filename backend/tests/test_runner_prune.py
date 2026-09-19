"""deploy/libris-runner-prune against a fake `docker`: only old, unused CI leftovers go."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "libris-runner-prune"
OLD = time.time() - 30 * 3600
RECENT = time.time() - 600
ANONYMOUS_OLD, ANONYMOUS_RECENT = "a" * 64, "b" * 64

FAKE_DOCKER = r"""#!PYTHON
import json, os, sys
path = os.environ["FAKE_DOCKER_STATE"]
state = json.load(open(path))
args = sys.argv[1:]
state["calls"].append(args)
json.dump(state, open(path, "w"))
if args[:2] == ["volume", "ls"]:
    print("\n".join(name for name, volume in state["volumes"].items() if volume["dangling"]))
elif args[:2] == ["volume", "inspect"]:
    volume = state["volumes"][args[-1]]
    print(f"{volume['created']} {volume.get('project', '')}")
elif args[:2] == ["network", "ls"]:
    print("\n".join(state["networks"]))
elif args[:2] == ["network", "inspect"]:
    network = state["networks"][args[-1]]
    print(f"{network['created']} {network['containers']}")
elif args[1:2] == ["rm"]:
    state[args[0] + "s"].pop(args[2])
    state["removed"].append(args[2])
    json.dump(state, open(path, "w"))
"""


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(tmp_path: Path, *arguments: str) -> tuple[subprocess.CompletedProcess, dict]:
    state = tmp_path / "state.json"
    if not state.exists():
        state.write_text(
            json.dumps(
                {
                    "calls": [],
                    "removed": [],
                    "volumes": {
                        ANONYMOUS_OLD: {"created": iso(OLD), "dangling": True},
                        ANONYMOUS_RECENT: {"created": iso(RECENT), "dangling": True},
                        "libris-e2e-42_books": {
                            "created": iso(OLD),
                            "dangling": True,
                            "project": "libris-e2e-42",
                        },
                        "relay_data": {"created": iso(OLD), "dangling": True, "project": "relay"},
                        "c" * 64: {"created": iso(OLD), "dangling": False},
                    },
                    "networks": {
                        "libris-e2e-42_default": {"created": int(OLD), "containers": 0},
                        "libris-e2e-43_default": {"created": int(OLD), "containers": 2},
                        "libris-e2e-44_default": {"created": int(RECENT), "containers": 0},
                    },
                }
            )
        )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER.replace("PYTHON", sys.executable))
    docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_DOCKER_STATE=str(state))
    result = subprocess.run(["bash", str(SCRIPT), *arguments], env=env, capture_output=True, text=True)
    return result, json.loads(state.read_text())


def test_dry_run_lists_without_removing(tmp_path):
    result, state = run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert state["removed"] == []
    assert f"Would remove volume {ANONYMOUS_OLD}" in result.stdout
    assert "3 unused Libris CI volume(s) and network(s) would be removed; 2 too recent kept." in result.stdout


def test_only_old_unused_ci_leftovers_are_removed(tmp_path):
    result, state = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert sorted(state["removed"]) == sorted([ANONYMOUS_OLD, "libris-e2e-42_books", "libris-e2e-42_default"])
    # Recent, in use, or another project's named volume: kept.
    assert {ANONYMOUS_RECENT, "relay_data", "c" * 64} <= set(state["volumes"])
    assert {"libris-e2e-43_default", "libris-e2e-44_default"} <= set(state["networks"])


def test_unknown_arguments_are_refused(tmp_path):
    result, _ = run(tmp_path, "--all")
    assert result.returncode == 64
