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
        done(state["services"].get(service, "migrate-container" if service == "migrate" else ""))
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
            if service == "migrate" and not state.get("migration_exit"):
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
    # A listing writes nothing: the script reads it through a process substitution, so it can still be
    # running while the loop removes images, and saving here would overwrite those removals.
    print("\n".join(lines))
    sys.exit(0)
if args[0] == "ps":
    done("\n".join(state["containers"]))
if args[0] == "inspect":
    container = state["containers"][args[-1]]
    if ".Image" in args[2]:
        done(container["image"])
    done("healthy" if "Health" in args[2] else "running")
if args[0] == "wait":
    done(str(state.get("migration_exit", 0)))
if args[0] == "run" and "cat" in args:
    img = image(args[4]) or next((i for i in state["images"].values() if i["id"] == args[4]), {})
    sys.stdout.write(img.get("compose", ""))  # the Compose file the image carries, byte for byte
    save()
    sys.exit(0 if img.get("compose") else 1)
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


def test_the_revision_is_checked_only_after_the_migration_has_exited(tmp_path, health):
    answer, url = health
    answer["version"] = "0.6.0"
    new = "3" * 40
    base, state_file, env = production(tmp_path)
    state = json.loads(state_file.read_text())
    state["images"]["new"] = image_state(new, "0.6.0")
    state["images"]["new-codex"] = {**image_state(new, "0.6.0"), "id": "sha256:c333333333333"}
    state["tags"].update({f"libris-production:{new}": "new", f"libris-codex-production:{new}": "new-codex"})
    state_file.write_text(json.dumps(state))
    arguments = (new, "0.6.0", "sha256:333333333333", "sha256:c333333333333")
    assert run({**env, "LIBRIS_PRODUCTION_HEALTH_URL": url}, *arguments).returncode == 0
    calls = json.loads(state_file.read_text())["calls"]
    up = next(i for i, call in enumerate(calls) if call[:1] == ["compose"] and "up" in call and "migrate" in call)
    assert "--wait" not in calls[up]  # returns while a one-shot container still runs
    waited = next(i for i, call in enumerate(calls) if call == ["wait", "migrate-container"])
    checked = next(i for i, call in enumerate(calls) if i > up and call[:1] == ["compose"] and "alembic_version" in call[-1])
    assert up < waited < checked


def test_a_failed_migration_stops_the_deployment_and_keeps_the_services_stopped(tmp_path, health):
    answer, url = health
    new = "3" * 40
    base, state_file, env = production(tmp_path)
    state = json.loads(state_file.read_text())
    state["images"]["new"] = image_state(new, "0.6.0")
    state["images"]["new-codex"] = {**image_state(new, "0.6.0"), "id": "sha256:c333333333333"}
    state["tags"].update({f"libris-production:{new}": "new", f"libris-codex-production:{new}": "new-codex"})
    state["migration_exit"] = 1
    state_file.write_text(json.dumps(state))
    result = run(
        {**env, "LIBRIS_PRODUCTION_HEALTH_URL": url}, new, "0.6.0", "sha256:333333333333", "sha256:c333333333333"
    )
    assert result.returncode != 0
    assert "Migration exited with status 1" in result.stderr
    assert (base / "current-version").read_text() != "0.6.0\n"


NEW_COMPOSE = "services:\n  api: {read_only: true}\n"


def with_new_version(state_file, compose=NEW_COMPOSE, head=HEAD):
    new = "3" * 40
    state = json.loads(state_file.read_text())
    state["images"]["new"] = {**image_state(new, "0.6.0", head), "compose": compose}
    state["images"]["new-codex"] = {**image_state(new, "0.6.0"), "id": "sha256:c333333333333"}
    state["tags"].update({f"libris-production:{new}": "new", f"libris-codex-production:{new}": "new-codex"})
    state_file.write_text(json.dumps(state))
    return (new, "0.6.0", "sha256:333333333333", "sha256:c333333333333")


def test_the_deployment_installs_the_compose_file_of_its_version(tmp_path, health):
    answer, url = health
    answer["version"] = "0.6.0"
    base, state_file, env = production(tmp_path)
    arguments = with_new_version(state_file)
    result = run({**env, "LIBRIS_PRODUCTION_HEALTH_URL": url}, *arguments)
    assert result.returncode == 0, result.stderr
    assert "differs from the Compose file of 0.6.0" in result.stdout and "+  api: {read_only: true}" in result.stdout
    assert (base / "docker-compose.yml").read_text() == NEW_COMPOSE
    assert (base / "docker-compose.yml.before-0.6.0").read_text() == "services: {}\n"
    # Installed only once the services are stopped: the dump ran with the file in place until then.
    calls = json.loads(state_file.read_text())["calls"]
    assert any(call[:1] == ["run"] and "cat" in call for call in calls)
    # The deployed version's file is now the reference: no drift.
    assert run(env, "--check-compose").returncode == 0


def test_a_failed_deployment_puts_the_previous_compose_file_back(tmp_path, health):
    answer, url = health
    answer["version"] = "0.5.0"  # the new version never answers its own version: health fails
    base, state_file, env = production(tmp_path)
    arguments = with_new_version(state_file)
    result = run({**env, "LIBRIS_PRODUCTION_HEALTH_URL": url}, *arguments)
    assert result.returncode != 0
    assert "previous images restored" in result.stderr
    assert (base / "docker-compose.yml").read_text() == "services: {}\n"


def test_drift_of_the_installed_compose_file_is_reported(tmp_path):
    base, state_file, env = production(tmp_path)
    state = json.loads(state_file.read_text())
    state["images"]["current"]["compose"] = "services: {}\n"
    state_file.write_text(json.dumps(state))
    assert run(env, "--check-compose").returncode == 0
    (base / "docker-compose.yml").write_text("services: {edited: by hand}\n")
    result = run(env, "--check-compose")
    assert result.returncode == 1 and "Drift" in result.stdout and "+services: {}" in result.stdout


def test_an_image_without_compose_file_keeps_the_installed_one(tmp_path, health):
    answer, url = health
    answer["version"] = "0.6.0"
    base, state_file, env = production(tmp_path)
    arguments = with_new_version(state_file, compose="")
    result = run({**env, "LIBRIS_PRODUCTION_HEALTH_URL": url}, *arguments)
    assert result.returncode == 0, result.stderr
    assert "carries no Compose file" in result.stderr
    assert (base / "docker-compose.yml").read_text() == "services: {}\n"
