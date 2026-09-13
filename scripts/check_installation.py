"""Boot a disposable Docker installation; never use production configuration or volumes."""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="libris-install-") as directory:
    config = Path(directory) / "install.env"
    subprocess.run([sys.executable, str(root / "scripts/setup.py"), "--output", str(config)], check=True)
    content = config.read_text().replace("PORT=8088", "PORT=4188").replace(":8088", ":4188")
    config.write_text(content)
    env = {**os.environ, "LIBRIS_ENV_FILE": str(config)}
    command = ["docker", "compose", "--project-name", "libris-check-" + uuid.uuid4().hex[:8],
               "--env-file", str(config), "-f", str(root / "docker-compose.yml")]
    try:
        subprocess.run(command + ["up", "-d", "--build", "--wait"], env=env, check=True, cwd=root)
        with urllib.request.urlopen("http://127.0.0.1:4188/health") as response:
            assert json.load(response)["status"] == "ok"
        values = dict(line.split("=", 1) for line in content.splitlines() if "=" in line and not line.startswith("#"))
        request = urllib.request.Request("http://127.0.0.1:4188/api/auth/login",
            data=json.dumps({"username": values["BOOTSTRAP_USERNAME"], "password": values["BOOTSTRAP_PASSWORD"]}).encode(),
            headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:4188"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
        subprocess.run(command + ["exec", "-T", "api", "alembic", "check"], env=env, check=True, cwd=root)
        print("Fresh installation: health, initial login and migration consistency passed.")
    finally:
        subprocess.run(command + ["down", "--volumes"], env=env, check=True, cwd=root)
