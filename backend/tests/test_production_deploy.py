"""deploy/libris-production-deploy driven against a fake `docker`: rollback guards and image pruning."""

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "libris-production-deploy"
REGISTRY = "192.168.1.126:5050/dev/libris"
OLD, PREVIOUS, CURRENT = "1" * 40, "4" * 40, "2" * 40
HEAD = "a7c2e9d41f05"

# Answers the few docker and docker compose calls the script makes from a JSON state file, records
# every call, and applies tags, removals and service restarts to that state.
FAKE_DOCKER = r'''#!PYTHON
import json, os, sys
path = os.environ["FAKE_DOCKER_STATE"]
state = json.load(open(path))
args = sys.argv[1:]
state["calls"].append(args)

def save():
    json.dump(state, open(path, "w"))

def image(reference):
    reference = state["tags"].get(reference, reference)
    return state["images"].get(reference)

def fmt(template, img):
    if ".Id" in template or ".ID" in template:
        return img["id"]
    if "revision" in template:
        return img["commit"]
    return img["version"]

def done(output="", code=0):
    save()
    if output:
        print(output)
    sys.exit(code)

if args[0] == "compose":
    if "ps" in args:
        service = args[-1]
        done(state["services"].get(service, ""))
    if "exec" in args and "pg_dump" in args[-1]:
        done("dump")
    if "exec" in args:
        done(state["database_revision"])
    if "up" in args:
        for service in args[args.index("up") + 1:]:
            if service in state["services"]:
                ids = {"api": os.environ["LIBRIS_IMAGE"], "worker": os.environ["LIBRIS_IMAGE"],
                       "codex": os.environ.get("LIBRIS_CODEX_IMAGE", "")}
                state["containers"][state["services"][service]]["image"] = image(ids[service])["id"]
            if service == "migrate":
                state["database_revision"] = image(os.environ["LIBRIS_IMAGE"])["head"]
        done()
    done()
if args[:2] == ["image", "inspect"]:
    img = image(args[-1]) or next((i for i in state["images"].values() if i["id"] == args[-1]), None)
    done(fmt(args[3], img) if img else "", 0 if img else 1)
if args[:2] == ["image", "tag"]:
    source = image(args[2]) or next(i for i in state["images"].values() if i["id"] == args[2])
    key = next(k for k, v in state["images"].items() if v is source)
    state["tags"][args[3]] = key
    done()
if args[:2] == ["image", "rm"]:
    state["removed"].append(args[2])
    done()
if args[:2] == ["image", "ls"]:
    repository = args[-1]
    lines = []
    for reference in state["listing"]:
        name, _, tag = reference.rpartition(":") if "@" not in reference else (reference.split("@")[0], "", "")
        if name != repository:
            continue
        img = image(reference)
        if "--digests" in args:
            lines.append(f"{name} <none> {reference.split('@')[1]} {img['id']}")
        else:
            lines.append(f"{reference} {img['id']}")
    done("\n".join(lines))
if args[0] == "ps":
    done("\n".join(state["containers"]))
if args[0] == "inspect":
    container = state["containers"][args[-1]]
    if ".Image" in args[2]:
        done(container["image"])
    done("healthy" if "Health" in args[2] else "running")
if args[0] == "run":
    done(image(args[4])["head"])  # `alembic heads | sed` runs inside the image
done("unexpected call", 1)
'''


def image_state(commit, version, head=HEAD):
    return {"id": f"sha256:{commit[:12]}", "commit": commit, "version": version, "head": head}


def production(tmp_path, *, deployed="0.5.0", previous_head=HEAD, database_revision=HEAD):
    base = tmp_path / "production"
    base.mkdir()
    (base / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / "secret.env").write_text("POSTGRES_PASSWORD=x\n")
    (base / "current-version").write_text(deployed + "\n")
    (base / "current-commit").write_text(CURRENT + "\n")
    (base / "current-app-image-id").write_text(f"sha256:{CURRENT[:12]}\n")
    (base / "current-codex-image-id").write_text(f"sha256:c{CURRENT[:11]}\n")
    images = {
        "old": image_state(OLD, "0.3.0"),
        "previous": image_state(PREVIOUS, "0.4.1", previous_head),
        "previous-codex": {**image_state(PREVIOUS, "0.4.1"), "id": "sha256:c444444444444"},
        "current": image_state(CURRENT, deployed),
        "current-codex": {**image_state(CURRENT, deployed), "id": f"sha256:c{CURRENT[:11]}"},
        "foreign": {"id": "sha256:foreign", "commit": "", "version": "", "head": ""},
    }
    tags = {
        f"libris-production:{OLD}": "old",
        f"libris-production:{PREVIOUS}": "previous",
        "libris-production:previous-api": "previous",
        "libris-production:previous-worker": "previous",
        "libris-codex-production:previous": "previous-codex",
        f"libris-production:{CURRENT}": "current",
        f"libris-codex-production:{CURRENT}": "current-codex",
        f"{REGISTRY}@sha256:d0": "old",
        f"{REGISTRY}@sha256:d1": "current",
        "postgres:17-bookworm": "foreign",
    }
    state = {
        "calls": [],
        "removed": [],
        "images": images,
        "tags": tags,
        "listing": list(tags),
        "services": {"api": "c-api", "worker": "c-worker", "codex": "c-codex"},
        "containers": {
            "c-api": {"image": images["current"]["id"]},
            "c-worker": {"image": images["current"]["id"]},
            "c-codex": {"image": images["current-codex"]["id"]},
            "c-db": {"image": "sha256:foreign"},
        },
        "database_revision": database_revision,
    }
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER.replace("#!PYTHON", "#!" + sys.executable))
    fake.chmod(0o755)
    state_file = tmp_path / "docker.json"
    state_file.write_text(json.dumps(state))
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(state_file),
        "LIBRIS_PRODUCTION_BASE": str(base),
        "LIBRIS_PRODUCTION_SECRET_ENV": str(tmp_path / "secret.env"),
    }
    return base, state_file, env


def run(env, *arguments):
    return subprocess.run(["bash", str(SCRIPT), *arguments], env=env, capture_output=True, text=True, timeout=60)


pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")


def test_dry_run_lists_only_superseded_libris_images_and_removes_nothing(tmp_path):
    _, state_file, env = production(tmp_path)
    result = run(env, "--prune-images", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"Would remove libris-production:{OLD}",
        f"Would remove {REGISTRY}@sha256:d0",
    ]
    assert json.loads(state_file.read_text())["removed"] == []


def test_pruning_keeps_the_deployed_and_previous_images_and_touches_nothing_else(tmp_path):
    _, state_file, env = production(tmp_path)
    result = run(env, "--prune-images")
    assert result.returncode == 0, result.stderr
    assert json.loads(state_file.read_text())["removed"] == [f"libris-production:{OLD}", f"{REGISTRY}@sha256:d0"]


def test_rollback_refuses_when_the_schema_changed_and_changes_nothing(tmp_path):
    base, state_file, env = production(tmp_path, previous_head="f3a91c07d2be")
    result = run(env, "--rollback")
    assert result.returncode == 65
    assert "Restore the pre-deployment dump" in result.stderr
    calls = json.loads(state_file.read_text())["calls"]
    assert not any(call[:1] == ["compose"] and ("stop" in call or "up" in call) for call in calls)
    assert (base / "current-version").read_text() == "0.5.0\n"


def test_rollback_never_goes_forward(tmp_path):
    _, _, env = production(tmp_path, deployed="0.4.0")
    result = run(env, "--rollback")
    assert result.returncode == 65 and "not older than the deployed 0.4.0" in result.stderr


def test_an_ordinary_deployment_still_refuses_an_older_version(tmp_path):
    _, _, env = production(tmp_path)
    result = run(env, PREVIOUS, "0.4.1", "sha256:444444444444", "sha256:c444444444444")
    assert result.returncode == 65 and "use --rollback" in result.stderr


@pytest.fixture
def health():
    answer = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "ok", "version": answer["version"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield answer, f"http://127.0.0.1:{server.server_port}/health"
    server.shutdown()


def test_rollback_redeploys_the_previous_images_when_the_schema_is_unchanged(tmp_path, health):
    answer, url = health
    answer["version"] = "0.4.1"
    base, state_file, env = production(tmp_path)
    result = run({**env, "LIBRIS_PRODUCTION_HEALTH_URL": url}, "--rollback")
    assert result.returncode == 0, result.stderr
    assert "rolled back from 0.5.0 to 0.4.1" in result.stdout
    assert (base / "current-version").read_text() == "0.4.1\n"
    assert (base / "current-commit").read_text() == PREVIOUS + "\n"
    state = json.loads(state_file.read_text())
    assert state["containers"]["c-api"]["image"] == "sha256:444444444444"
    assert state["containers"]["c-worker"]["image"] == "sha256:444444444444"
    # The version just removed becomes the retained `previous-*`, and the older one is pruned.
    assert state["tags"]["libris-production:previous-api"] == "current"
    assert f"libris-production:{OLD}" in state["removed"]
    assert len(list((base / "backups").glob("pre-*.dump"))) == 1


def test_a_newer_version_is_deployed_and_the_replaced_one_is_retained(tmp_path, health):
    answer, url = health
    answer["version"] = "0.6.0"
    new = "3" * 40
    base, state_file, env = production(tmp_path)
    state = json.loads(state_file.read_text())
    state["images"]["new"] = image_state(new, "0.6.0")
    state["images"]["new-codex"] = {**image_state(new, "0.6.0"), "id": "sha256:c333333333333"}
    state["tags"].update({f"libris-production:{new}": "new", f"libris-codex-production:{new}": "new-codex"})
    state_file.write_text(json.dumps(state))
    result = run(
        {**env, "LIBRIS_PRODUCTION_HEALTH_URL": url}, new, "0.6.0", "sha256:333333333333", "sha256:c333333333333"
    )
    assert result.returncode == 0, result.stderr
    assert (base / "current-version").read_text() == "0.6.0\n"
    state = json.loads(state_file.read_text())
    assert state["containers"]["c-api"]["image"] == "sha256:333333333333"
    assert state["tags"]["libris-production:previous-api"] == "current"
    # 0.4.1 was the previous version before this deployment: it is no longer needed.
    assert set(state["removed"]) == {f"libris-production:{OLD}", f"libris-production:{PREVIOUS}", f"{REGISTRY}@sha256:d0"}
