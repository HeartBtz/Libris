"""Print the CHANGELOG section of one release, or publish it as that tag's GitLab release.

    python3 scripts/release_notes.py v0.5.0            # print the notes
    python3 scripts/release_notes.py v0.5.0 --publish  # create or update the GitLab release (CI only)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def section(changelog: str, version: str) -> str:
    """Body of `## [version]`, up to the next release heading or the link definitions."""
    heading = re.compile(rf"^## \[{re.escape(version)}\](?:\s|$)")
    lines = changelog.splitlines()
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        raise ValueError(f"CHANGELOG.md has no section for {version}")
    body = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or re.match(r"^\[[^\]]+\]: ", line):
            break
        body.append(line)
    notes = "\n".join(body).strip()
    if not notes:
        raise ValueError(f"The CHANGELOG.md section for {version} is empty")
    return notes + "\n"


def api(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"JOB-TOKEN": token, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {"message": error.read().decode(errors="replace")[:500]}


def publish(tag: str, notes: str, environ=os.environ, call=api) -> str:
    """Create the release, or update its notes when a previous run already created it."""
    project = f"{environ['CI_API_V4_URL']}/projects/{environ['CI_PROJECT_ID']}/releases"
    token = environ["CI_JOB_TOKEN"]
    release = {"tag_name": tag, "name": f"Libris {tag}", "description": notes}
    status, body = call("POST", project, token, release)
    if status == 201:
        return "created"
    if status == 409:
        update = {"name": f"Libris {tag}", "description": notes}
        status, body = call("PUT", f"{project}/{urllib.parse.quote(tag, safe='')}", token, update)
        if status == 200:
            return "updated"
    raise RuntimeError(f"GitLab release {tag}: HTTP {status}: {body.get('message', body)}")


def main(arguments: list[str]) -> None:
    if len(arguments) not in (1, 2) or (len(arguments) == 2 and arguments[1] != "--publish"):
        raise SystemExit(__doc__)
    tag = arguments[0]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise SystemExit(f"Not a release tag: {tag}")
    try:
        notes = section(CHANGELOG.read_text(encoding="utf-8"), tag[1:])
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if len(arguments) == 2:
        print(f"GitLab release {tag} {publish(tag, notes)}")
    else:
        sys.stdout.write(notes)


if __name__ == "__main__":
    main(sys.argv[1:])
