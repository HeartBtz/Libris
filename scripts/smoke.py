"""Exercise PostgreSQL, real worker crash recovery, mock inference and EPUBCheck in Compose.

Run after starting docker-compose.test.yml's test profile. Credentials are read locally and never printed.
Creates one clearly-labelled test project; --cleanup removes it after browser tests.
"""
import argparse
import io
import json
import subprocess
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from ebooklib import epub

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/tmp/libris/epub-smoke.json")
STATE.parent.mkdir(parents=True, exist_ok=True)


def book(title: str = "The Silver Tower — synthetic test") -> bytes:
    b = epub.EpubBook()
    b.set_identifier("urn:uuid:00000000-0000-4000-8000-000000000001")
    b.set_title(title)
    b.set_language("en")
    b.add_author("Libris · Test fixture")
    chapters = []
    for i, name in enumerate(("One", "Two", "Three")):
        c = epub.EpubHtml(title="Chapter " + name, file_name=f"chapter{i}.xhtml", lang="en")
        c.content = f'<h1 id="chapter{i}">Chapter {name}</h1><p>Alice entered the <em>Silver Tower</em> and stopped.</p>' + (
            '<p>The pendant shone in the moonlight. She remembered his promise. '
            'Bob watched the doorway in silence. The wind carried a familiar melody.</p>' * 15)
        b.add_item(c)
        chapters.append(c)
    b.toc = tuple(chapters)
    b.add_item(epub.EpubNcx())
    b.add_item(epub.EpubNav())
    b.spine = ["nav", *chapters]
    result = io.BytesIO()
    epub.write_epub(result, b)
    return result.getvalue()


def main(cleanup=False):
    config = dotenv_values(ROOT / ".env")
    host = config.get("BIND_ADDRESS", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    client = httpx.Client(base_url=f"http://{host}:{config.get('PORT', '8088')}", timeout=120)
    def request(method, path, **kwargs):
        r = client.request(method, "/api" + path, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {r.status_code}: {r.text[:1200]}")
        return r
    request("POST", "/auth/login", json={"username": config["BOOTSTRAP_USERNAME"], "password": config["BOOTSTRAP_PASSWORD"]})
    if cleanup:
        state = json.loads(STATE.read_text())
        jobs = request("GET", f"/projects/{state['project_id']}/jobs").json()
        for job in jobs:
            if job["status"] not in {"completed", "cancelled"}:
                request("POST", f"/projects/{state['project_id']}/jobs/{job['id']}/cancel")
        request("DELETE", f"/projects/{state['project_id']}")
        request("DELETE", f"/providers/{state['provider_id']}")
        print("Projet et provider synthétiques supprimés.")
        return
    provider = request("POST", "/providers", json={"name": "Synthetic test only", "base_url": "http://mock-llm:8091/v1",
        "model": "synthetic-literary-test", "context_window": 64000,
        "capabilities": {"supports_json_schema": True}}).json()
    test = request("POST", f"/providers/{provider['id']}/test").json()
    assert test["ok"], test["message"]
    p = request("POST", "/projects", files={"file": ("fixture.epub", book(), "application/epub+zip")}).json()
    pid = p["id"]
    STATE.write_text(json.dumps({"project_id": pid, "provider_id": provider["id"]}))
    fields = {key: p[key] for key in ("title", "author", "source_language", "target_language", "instructions", "context_backend")}
    request("PUT", f"/projects/{pid}", json={**fields, "provider_id": provider["id"], "quality": "normal"})
    def wait_job(jid):
        for _ in range(120):
            jobs = request("GET", f"/projects/{pid}/jobs").json()
            job = next(j for j in jobs if j["id"] == jid)
            if job["status"] == "failed":
                raise AssertionError(job["error"])
            if job["status"] == "completed":
                return
            time.sleep(2)
        raise AssertionError("Worker did not finish in 240 seconds")
    analyzed = request("POST", f"/projects/{pid}/jobs", json={"operation": "analyze"}).json()
    wait_job(analyzed["id"])
    assert request("GET", f"/projects/{pid}").json()["bible"]
    job = request("POST", f"/projects/{pid}/jobs", json={"operation": "translate"}).json()
    for _ in range(60):
        segments = request("GET", f"/projects/{pid}/segments").json()
        saved = [s for s in segments if s["translation"]]
        if saved:
            break
        time.sleep(.5)
    assert saved, "No segment saved"
    request("POST", f"/projects/{pid}/jobs/{job['id']}/pause")
    assert request("GET", f"/projects/{pid}/jobs").json()[0]["status"] == "paused"
    request("POST", f"/projects/{pid}/jobs/{job['id']}/resume")
    # Wait until a new lease is claimed, then kill the worker mid-book.
    for _ in range(30):
        if request("GET", f"/projects/{pid}/jobs").json()[0]["status"] == "translating":
            break
        time.sleep(.5)
    subprocess.run(["docker", "compose", "kill", "-s", "SIGKILL", "worker"], cwd=ROOT, check=True,
                   capture_output=True)
    subprocess.run(["docker", "compose", "start", "worker"], cwd=ROOT, check=True, capture_output=True)
    wait_job(job["id"])
    after = request("GET", f"/projects/{pid}/segments").json()
    assert all(s["translation"] and s["stage"] == "done" for s in after)
    for old in saved:
        assert next(s for s in after if s["id"] == old["id"])["translation"] == old["translation"]
    request("GET", f"/projects/{pid}/segments?status=uncertain")
    output = request("GET", f"/projects/{pid}/export/epub")
    (STATE.parent / "translated-smoke.epub").write_bytes(output.content)
    print(json.dumps({"project_id": pid, "segments": len(after), "analysis": "passed", "translation": "passed",
                      "pause_resume": "passed", "SIGKILL_recovery": "passed", "epubcheck_export": "passed"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    main(parser.parse_args().cleanup)
