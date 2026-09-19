"""Provider comparison (audit I-33): same sample, several providers, nothing written to the book."""

import httpx
import respx
from sqlalchemy import select
from test_pipeline import mock_completion

from app.db import SessionLocal
from app.maintenance.compare_providers import OPERATION, compare, sample_passages, table
from app.models import Provider, RequestLog, Segment


def second_provider(url: str, name: str, cost: float) -> str:
    with SessionLocal() as db:
        provider = Provider(
            name=name, base_url=url, model=f"{name}-model", capabilities={"supports_json_schema": True},
            context_window=64000, input_cost=cost, output_cost=cost,
        )  # fmt: skip
        db.add(provider)
        db.commit()
        return provider.id


@respx.mock
async def test_the_same_sample_goes_through_each_provider(seeded):
    pid, _, first = seeded
    with SessionLocal() as db:
        db.get(Provider, first).input_cost = 2.0
        db.commit()
    down = second_provider("https://down.test/v1", "Down", 1.0)
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    respx.post("https://down.test/v1/chat/completions").mock(return_value=httpx.Response(503))
    with SessionLocal() as db:
        before = [
            (s.id, s.translation, s.status)
            for s in db.scalars(select(Segment).where(Segment.project_id == pid))
        ]
        sample = [s.id for s in sample_passages(db, pid, 2)]
    assert sample
    report = await compare(pid, [first, down], sample=2)
    good, bad = report["providers"]
    assert report["sample"] == sample
    assert (good["translated"], good["passages"], good["failed"]) == (len(sample), len(sample), [])
    assert good["calls"] == len(sample) and good["input_tokens"] == 120 * len(sample)
    assert good["cost"] > 0 and good["seconds_mean"] is not None
    assert bad["translated"] == 0 and len(bad["failed"]) == len(sample)
    assert "ProviderUnavailable" in bad["failed"][0]["error"]
    passage = report["side_by_side"][sample[0]]
    assert passage["source"] and "Mock" in passage and "Down" in passage
    assert "Mock" in table(report) and "Down-model" in table(report)
    with SessionLocal() as db:
        # Nothing is written to the book; the calls are recorded under their own operation.
        after = [
            (s.id, s.translation, s.status)
            for s in db.scalars(select(Segment).where(Segment.project_id == pid))
        ]
        assert after == before
        operations = set(db.scalars(select(RequestLog.operation).where(RequestLog.project_id == pid)))
        assert operations == {OPERATION}
