"""Remove only the two known browser-test projects from the interrupted test run."""
from pathlib import Path
import httpx
from dotenv import dotenv_values

config = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
host = config.get("BIND_ADDRESS", "127.0.0.1")
with httpx.Client(base_url=f"http://{host}:{config.get('PORT', '8088')}", timeout=20) as client:
    client.post("/api/auth/login", json={"username": config["BOOTSTRAP_USERNAME"], "password": config["BOOTSTRAP_PASSWORD"]}).raise_for_status()
    for pid in ("eaa81b20-db96-41ca-bda1-56c8cdd550ca", "8b5fe7d9-ef60-41e7-ade0-ad0a338fb6e2"):
        response = client.get(f"/api/projects/{pid}")
        if response.status_code == 404:
            continue
        response.raise_for_status()
        project = response.json()
        if project["title"] not in {"Batch fixture A", "Batch fixture B"}:
            raise RuntimeError("Le projet de test a été renommé ; nettoyage arrêté.")
        client.delete(f"/api/projects/{pid}?stop_jobs=true").raise_for_status()
        print("Projet de test supprimé :", project["title"])
