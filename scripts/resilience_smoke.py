"""Local outage, manual pause, SIGTERM and retry checks on an explicitly synthetic project."""
import json
import subprocess
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from smoke import book

ROOT = Path(__file__).resolve().parents[1]
config = dotenv_values(ROOT / ".env")
host = config.get("BIND_ADDRESS", "127.0.0.1")
client = httpx.Client(base_url=f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{config.get('PORT','8088')}", timeout=30)


def req(method, path, **kwargs):
    response = client.request(method, "/api" + path, **kwargs)
    response.raise_for_status()
    return response.json()


def docker(*args):
    result = subprocess.run(["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.test.yml",
                             "--profile", "test", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout


def control(available=True, delay=2):
    code = "import urllib.request; r=urllib.request.Request('http://127.0.0.1:8091/control',data=" + repr(
        json.dumps({"available": available, "delay": delay}).encode()) + ",headers={'Content-Type':'application/json'}); urllib.request.urlopen(r)"
    docker("exec", "-T", "mock-llm", "python", "-c", code)


def wait(check, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(.4)
    raise AssertionError("Test de reprise : état attendu non atteint.")


def main():
    req("POST", "/auth/login", json={"username": config["BOOTSTRAP_USERNAME"], "password": config["BOOTSTRAP_PASSWORD"]})
    provider = req("POST", "/providers", json={"name": "Resilience fixture provider", "base_url": "http://mock-llm:8091/v1",
        "model": "synthetic-literary-test", "context_window": 64000, "capabilities": {"supports_json_schema": True}})
    project = req("POST", "/projects", files={"file": ("resilience.epub", book())})
    pid = project["id"]
    try:
        req("PUT", f"/projects/{pid}", json={"title": "Resilience fixture", "provider_id": provider["id"],
                                             "quality": "fast", "context_backend": "internal"})
        req("PUT", f"/projects/{pid}/bible", json={"summary": "Synthetic book about Alice, Bob and a pendant."})
        control(available=False)
        job = req("POST", f"/projects/{pid}/jobs", json={"operation": "translate"})
        def current():
            return next(j for j in req("GET", f"/projects/{pid}/jobs") if j["id"] == job["id"])
        def segments():
            return req("GET", f"/projects/{pid}/segments")
        wait(lambda: current()["status"] == "waiting", 30)
        assert current()["next_attempt"] > time.time()
        assert not any(s["translation"] for s in segments())
        print("HTTP 503 → attente persistante : OK", flush=True)
        req("POST", f"/projects/{pid}/jobs/{job['id']}/pause")
        control(available=True, delay=3)
        docker("stop", "worker")
        docker("start", "worker")
        time.sleep(3)
        assert current()["status"] == "paused" and not any(s["translation"] for s in segments())
        print("Pause volontaire conservée après redémarrage : OK", flush=True)
        req("POST", f"/projects/{pid}/jobs/{job['id']}/resume")
        saved = wait(lambda: [s for s in segments() if s["stage"] == "done"])
        start = time.monotonic()
        docker("stop", "worker")
        assert time.monotonic() - start < 25
        assert current()["status"] == "pending"
        assert current()["stop_reason"] == "worker_interrupted"
        docker("start", "worker")
        wait(lambda: current()["status"] == "completed")
        after = {s["id"]: s for s in segments()}
        for segment in saved:
            assert after[segment["id"]]["translation"] == segment["translation"]
            assert after[segment["id"]]["revision"] == segment["revision"]
        print("SIGTERM pendant l’inférence → reprise sans retraduire les étapes terminées : OK", flush=True)
        target = segments()[0]
        control(delay=20)
        job = req("POST", f"/projects/{pid}/jobs", json={"operation": "translate", "segment_id": target["id"],
            "force": True, "instruction": "Synthetic in-flight pause test."})
        def active():
            return [r for r in req("GET", f"/projects/{pid}/requests") if r.get("job_id") == job["id"] and r["status"] == "running"]
        wait(active)
        req("POST", f"/projects/{pid}/jobs/{job['id']}/pause")
        wait(lambda: any(r.get("job_id") == job["id"] and r["status"] == "interrupted"
                        for r in req("GET", f"/projects/{pid}/requests")), 10)
        assert current()["status"] == "paused"
        assert segments()[0]["revision"] == target["revision"]
        print("Pause en cours de requête → interruption auditée, traduction préservée : OK", flush=True)
    finally:
        control()
        docker("start", "worker")
        for job in req("GET", f"/projects/{pid}/jobs"):
            if job["status"] not in {"completed", "cancelled"}:
                req("POST", f"/projects/{pid}/jobs/{job['id']}/cancel")
        req("DELETE", f"/projects/{pid}")
        req("DELETE", f"/providers/{provider['id']}")


if __name__ == "__main__":
    main()
