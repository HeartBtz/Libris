"""Generate application-only secrets; never overwrite an existing installation."""
import argparse
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=root / ".env", help="New configuration path")
path = parser.parse_args().output
values = {
    "SECRET_KEY": secrets.token_urlsafe(48),
    "BOOTSTRAP_PASSWORD": secrets.token_urlsafe(24),
    "POSTGRES_PASSWORD": secrets.token_hex(32),
    "CODEX_BRIDGE_TOKEN": secrets.token_urlsafe(48),
}
content = (root / ".env.example").read_text()
for name, value in values.items():
    content = content.replace(f"{name}=\n", f"{name}={value}\n")
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w") as stream:
    stream.write(content)
print(f"Configuration créée dans {path} (mode 0600). Le mot de passe admin est BOOTSTRAP_PASSWORD.")
