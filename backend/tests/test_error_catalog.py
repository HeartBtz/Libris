"""Every error message the API can answer has an English version."""

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.i18n import MESSAGES, TEMPLATES, english, localize, preferred_language
from app.main import app

APP = Path(__file__).resolve().parents[1] / "app"
RAISED = {"HTTPException", "ValueError", "LLMError", "ProviderUnavailable", "ProviderAuthenticationRequired"}
PASSWORD = "test-password-123456789"


def message(node) -> list[str | None] | None:
    """The literal parts of a message; None stands for a value computed at run time."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [part.value if isinstance(part, ast.Constant) else None for part in node.values]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (message(node.left) or [None]) + (message(node.right) or [None])
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "message":
                return message(value)
    return None


def user_messages() -> dict[str, list[str | None]]:
    """Messages of raised errors, `{"detail": ...}` answers and `*_message` helpers, by location."""
    found = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            candidates = []
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in RAISED:
                arguments = node.args[1:2] if node.func.id == "HTTPException" else node.args[:1]
                candidates = arguments
            elif isinstance(node, ast.Dict):
                candidates = [
                    value
                    for key, value in zip(node.keys, node.values, strict=True)
                    if isinstance(key, ast.Constant) and key.value == "detail"
                ]
            elif isinstance(node, ast.FunctionDef) and node.name.endswith("_message"):
                candidates = [n.value for n in ast.walk(node) if isinstance(n, ast.Return) and n.value]
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "error" and len(node.args) > 1:
                candidates = node.args[1:2]
            for candidate in candidates:
                parts = message(candidate)
                if parts and any(parts):  # a message computed elsewhere is checked where it is written
                    found[f"{path.relative_to(APP.parent)}:{candidate.lineno}"] = parts
    return found


def sample(parts: list[str | None]) -> tuple[str, list[str]]:
    values = [f"Valeur{index}" for index, part in enumerate(parts) if part is None]
    iterator = iter(values)
    return "".join(part if part is not None else next(iterator) for part in parts), values


def test_every_user_message_of_the_code_has_an_english_version():
    found = user_messages()
    assert len(found) > 150, "the static scan no longer sees the messages"
    missing = []
    for where, parts in found.items():
        text, values = sample(parts)
        translated = english(text)
        if translated is None or any(value not in translated for value in values):
            missing.append(f"{where}: {text}")
    assert not missing, "Add these messages to app/i18n.py:\n" + "\n".join(missing)


def test_messages_built_outside_a_raise_are_translated_too():
    limits = (APP / "limits.py").read_text()
    assert "Authentification requise." in limits and "Mo au maximum." in limits
    assert english("Authentification requise.") == "Authentication required."
    assert english("Requête trop volumineuse : 60 Mo au maximum.") == "Request too large: 60 MB at most."


def test_the_catalog_has_no_duplicate_or_untranslatable_entry():
    assert not set(MESSAGES) & set(TEMPLATES)
    for french, english_text in TEMPLATES.items():
        placeholders = sorted(part for part in french.split("{")[1:])
        assert all(f"{{{p.split('}')[0]}}}" in english_text for p in placeholders), french


@pytest.mark.parametrize(
    ("header", "language"),
    [
        (None, "fr"),
        ("", "fr"),
        ("en", "en"),
        ("en-GB", "en"),
        ("fr-FR,fr;q=0.9,en;q=0.8", "fr"),
        ("en-US,en;q=0.9,fr;q=0.8", "en"),
        ("fr;q=0.4, en;q=0.6", "en"),
        ("de-DE", "fr"),
        ("en;q=bad", "fr"),
    ],
)
def test_the_preferred_language_follows_accept_language(header, language):
    assert preferred_language(header) == language


def test_values_inside_messages_are_carried_over():
    assert english("Cet EPUB est déjà importé dans « Dune » .") is None
    assert english("Cet EPUB est déjà importé dans « Dune ».") == "This EPUB is already imported in “Dune”."
    detail = {"message": "EPUBCheck signale un EPUB invalide : « Dune ».", "errors": ["RSC-005 — x", "… et 3 autre(s) erreur(s)."], "book": "Dune"}
    assert localize(detail, "en") == {
        "message": "EPUBCheck reports an invalid EPUB: “Dune”.",
        "errors": ["RSC-005 — x", "… and 3 more error(s)."],
        "book": "Dune",
    }
    assert localize(detail, "fr") is detail


def test_error_answers_follow_the_interface_language(seeded):
    with TestClient(app) as client:
        anonymous = client.get("/api/projects", headers={"Accept-Language": "en"})
        assert anonymous.json()["detail"] == "Sign-in required."
        assert client.get("/api/projects").json()["detail"] == "Connexion nécessaire."
        client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD})
        missing = client.get("/api/projects/unknown", headers={"Accept-Language": "en"})
        assert (missing.status_code, missing.json()["detail"]) == (404, "Project not found.")
        french = client.get("/api/projects/unknown", headers={"Accept-Language": "fr-FR,en;q=0.5"})
        assert french.json()["detail"] == "Projet introuvable."
        invalid = client.post(
            "/api/providers",
            headers={"Accept-Language": "en"},
            json={"name": "x", "base_url": "ftp://host", "model": "m"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"][0]["msg"] == "Value error, An HTTP(S) URL without embedded credentials is required."
        unknown_route = client.get("/api/nothing-here", headers={"Accept-Language": "en"})
        assert unknown_route.json()["detail"] == "Unknown API route."
    with TestClient(app) as client:
        body_limit = client.post("/api/projects", headers={"Accept-Language": "en"}, content=b"x" * (2 * 1024**2))
        assert (body_limit.status_code, body_limit.json()["detail"]) == (401, "Authentication required.")
