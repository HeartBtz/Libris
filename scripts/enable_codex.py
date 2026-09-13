"""Add one dedicated bridge credential without importing any personal Codex authentication."""
import os
import secrets
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".env"
text = path.read_text()
lines = text.splitlines()
existing = next((line.split("=", 1)[1] for line in lines if line.startswith("CODEX_BRIDGE_TOKEN=")), "")
if not existing:
    lines = [line for line in lines if not line.startswith("CODEX_BRIDGE_TOKEN=")]
    lines.append("CODEX_BRIDGE_TOKEN=" + secrets.token_urlsafe(48))
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
print("Connecteur préparé. Démarrez : docker compose --profile codex up -d --build")
