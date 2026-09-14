import json
import os
import urllib.request
from pathlib import Path


def request_json(
    url: str, *, data: dict[str, str], method: str = "POST", token: str = ""
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


username = os.environ["DOCKERHUB_USERNAME"]
password = os.environ["DOCKERHUB_TOKEN"]
overview = Path("docs/docker-hub.md").read_text()
login = request_json(
    "https://hub.docker.com/v2/users/login/",
    data={"username": username, "password": password},
)
access_token = login.get("access_token") or login.get("token")
if not access_token:
    raise SystemExit("Docker Hub authentication did not return an access token")

repository = request_json(
    f"https://hub.docker.com/v2/repositories/{username.lower()}/libris/",
    method="PATCH",
    token=access_token,
    data={
        "description": "Self-hosted, context-aware EPUB translation with resumable jobs and human review.",
        "full_description": overview,
    },
)
if repository.get("full_description") != overview:
    raise SystemExit("Docker Hub did not retain the expected overview")
print("Docker Hub overview published")
