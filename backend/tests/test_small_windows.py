import pytest
import respx
from epubs import epub_bytes
from sqlalchemy import select
from test_pipeline import mock_completion, run_job

from app.api.projects import import_book
from app.db import SessionLocal
from app.engines.context.builder import ContextTooLarge, build_context
from app.models import Project, Provider, RequestLog, Segment, User
from app.security import password_hash

PARAGRAPH = "アリスは銀の塔に入った。" * 700


def small_book(window: int, output: int) -> tuple[str, str]:
    with SessionLocal() as db:
        user = User(username="tester", password_hash=password_hash("test-password-123456789"), admin=True)
        provider = Provider(
            name="Small",
            base_url="https://llm.test/v1",
            model="small",
            capabilities={"supports_json_schema": True},
            context_window=window,
            max_output_tokens=output,
        )
        db.add_all([user, provider])
        db.flush()
        project = import_book(db, user.id, epub_bytes(f"<h1>一</h1><p>{PARAGRAPH}</p>", language="ja"))
        project.provider_id, project.quality, project.context_backend = provider.id, "fast", "internal"
        project.bible = {"summary": "test"}
        db.commit()
        segment = db.scalar(
            select(Segment.id).where(Segment.project_id == project.id, Segment.source.contains("銀"))
        )
        return project.id, segment


async def test_the_error_names_the_numbers_of_the_window():
    pid, sid = small_book(8192, 4096)
    with pytest.raises(ContextTooLarge) as error:
        await build_context(pid, sid, "translation")
    message = str(error.value)
    assert "Fenêtre de 8192 tokens" in message and "4096 tokens réservés à la réponse" in message
    assert "texte du passage" in message and "réduisez sa sortie maximale" in message
    assert "réduisez les instructions" not in message


@respx.mock
async def test_a_small_window_translates_a_long_passage_in_parts():
    pid, sid = small_book(16384, 4096)
    route = respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    job = await run_job(pid, "translate")
    assert job.status == "completed", job.error
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        assert segment.status != "error" and segment.translation
        # The parts were reassembled into the original units, in order.
        assert [u["id"] for u in segment.translated_units] == [u["id"] for u in segment.units]
        assert "".join(u["text"] for u in segment.translated_units) == "".join(
            u["text"] for u in segment.units
        )
        calls = db.scalars(select(RequestLog).where(RequestLog.segment_id == sid)).all()
        assert len(calls) > 2
        project = db.get(Project, pid)
        assert project.provider_id
    assert route.call_count >= len(calls)
