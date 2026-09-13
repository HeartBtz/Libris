"""Compact, named OpenViking mirrors. These editorial documents are not narrative retrieval evidence."""

import hashlib
import html
import json
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engines.memory.identities import canonical_bible, effective_names, identities
from app.models import Chapter, CharacterRelation, Memory, Outbox, Project, Segment
from app.providers.openviking import project_uri

CATALOG_NAME = re.compile(r"(?:book\.md|book-bible\.json|(?:characters|relationships)(?:-\d{4})?\.json)\Z")


def catalog_uri(project: Project, filename: str) -> str:
    if not CATALOG_NAME.fullmatch(filename):
        raise ValueError("Document de catalogue inconnu.")
    return project_uri(project) + "/" + filename


def files_for(db: Session, project: Project) -> dict[str, str]:
    root = project_uri(project)
    files = {}
    people = identities(db, project.id)
    names = {p.id: p.name for p in people}
    analyzed = db.scalar(
        select(func.count())
        .select_from(Memory)
        .where(Memory.project_id == project.id, Memory.kind == "analysis")
    )
    total = db.scalar(select(func.count()).select_from(Segment).where(Segment.project_id == project.id))
    synthesized = db.scalar(
        select(func.count())
        .select_from(Chapter)
        .where(Chapter.project_id == project.id, Chapter.analyzed.is_(True))
    )
    links = {
        "book-bible": f"{root}/book-bible.json",
        "characters": f"{root}/characters.json",
        "relationships": f"{root}/relationships.json",
    }
    files["book.md"] = (
        f"# {html.escape(project.title)}\n\nAuteur : {html.escape(project.author)}\n\nLangues : {project.source_language} → {project.target_language}\n\n"
        f"Passages analysés : {analyzed}/{total}. Sections synthétisées : {synthesized}.\n\n"
        + "\n".join(f"- [{name}]({uri})" for name, uri in links.items())
        + "\n\nCes documents sont des miroirs contextuels de l’application. Les décisions canoniques restent en SQL.\n"
    )
    overview = {k: v for k, v in canonical_bible(db, project).items() if k != "characters"}
    files["book-bible.json"] = json.dumps(
        {"title": project.title, "bible": overview, "characters_uri": links["characters"]}, ensure_ascii=False
    )

    def pages(kind: str, entries: list[dict]):
        chunks: list[list[dict]] = []
        current: list[dict] = []
        size = 0
        for entry in entries:
            length = len(json.dumps(entry, ensure_ascii=False).encode())
            if current and size + length > 10000:
                chunks.append(current)
                current, size = [], 0
            current.append(entry)
            size += length
        if current:
            chunks.append(current)
        uris = []
        for index, chunk in enumerate(chunks):
            filename = f"{kind}-{index:04}.json"
            uris.append(f"{root}/{filename}")
            files[filename] = json.dumps({"book": project.title, "entries": chunk}, ensure_ascii=False)
        files[kind + ".json"] = json.dumps(
            {"book": project.title, "count": len(entries), "pages": uris}, ensure_ascii=False
        )

    pages(
        "characters",
        [
            {
                "id": p.id,
                "canonical_name": p.name,
                "aliases": [n for n in effective_names(p, people) if n != p.name],
                "identity_confirmed": p.identity_validated,
                "profile_validated": p.validated,
                "role": str(p.data.get("role", ""))[:300],
                "description": str(p.data.get("description", ""))[:1200],
                "gender": p.data.get("gender", ""),
                "pronouns": p.data.get("pronouns", ""),
                "speech_style": str(p.data.get("speech_style", ""))[:600],
            }
            for p in people
        ],
    )
    edges = db.scalars(
        select(CharacterRelation).where(
            CharacterRelation.project_id == project.id, CharacterRelation.active.is_(True)
        )
    ).all()
    pages(
        "relationships",
        [
            {
                "id": edge.id,
                "source_id": edge.source_id,
                "source": names.get(edge.source_id),
                "target_id": edge.target_id,
                "target": names.get(edge.target_id),
                "type": edge.relation_type,
                "description": edge.description[:1000],
                "description_truncated": len(edge.description) > 1000,
                "evidence": edge.evidence[:600],
                "position": edge.position,
                "human_validated": edge.validated,
                "provenance": edge.provenance,
                "characters_uri": links["characters"],
            }
            for edge in edges
        ],
    )
    return files


def queue_catalog(db: Session, project: Project, force: bool = False) -> Outbox | None:
    if project.context_backend == "internal":
        return None
    files = files_for(db, project)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    key = f"catalog:{project.id}"
    entry = db.scalar(select(Outbox).where(Outbox.event_key == key))
    if entry and entry.status == "pending" and not entry.error:
        entry.next_attempt = 0
    if entry and entry.payload.get("fingerprint") == digest and not force:
        return entry
    payload = {"type": "project_catalog", "fingerprint": digest, "files": files}
    if entry:
        previously_sent = entry.status == "sent"
        entry.payload, entry.status = payload, "pending"
        if force or previously_sent:
            entry.next_attempt, entry.error = 0, ""
    else:
        entry = Outbox(project_id=project.id, event_key=key, session_name="catalog", payload=payload)
        db.add(entry)
    db.flush()
    return entry


def schedule_catalogs() -> None:
    from app.db import SessionLocal

    with SessionLocal() as db:
        projects = db.scalars(select(Project).where(Project.context_backend != "internal")).all()
        for project in projects:
            # Don't publish a book merely because it was uploaded; analysis or a human bible opts it in.
            has_analysis = db.scalar(
                select(Memory.id).where(Memory.project_id == project.id, Memory.kind == "analysis").limit(1)
            )
            if has_analysis or project.bible:
                queue_catalog(db, project)
        db.commit()
