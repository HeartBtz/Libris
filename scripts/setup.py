"""Generate application-only secrets; never overwrite an existing installation."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
values = {
    "SECRET_KEY": secrets.token_urlsafe(48),
    "BOOTSTRAP_PASSWORD": secrets.token_urlsafe(24),
    "POSTGRES_PASSWORD": secrets.token_hex(32),
}
content = (root / ".env.example").read_text()
for name, value in values.items():
    content = content.replace(f"{name}=\n", f"{name}={value}\n")
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w") as stream:
    stream.write(content)
print(f"Configuration créée dans {path} (mode 0600). Le mot de passe admin est BOOTSTRAP_PASSWORD.")
