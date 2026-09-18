import json
import threading
import time
from pathlib import Path

import pytest

from app.config import settings
from app.engines.epub import check


@pytest.fixture
def fake_java(monkeypatch):
    monkeypatch.setattr(settings(), "epubcheck_jar", "/opt/epubcheck/epubcheck.jar")
    monkeypatch.setattr(settings(), "epubcheck_concurrency", 2)
    state = {"running": 0, "peak": 0, "commands": []}
    lock = threading.Lock()

    def run(command, **_options):
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
            state["commands"].append(command)
        time.sleep(0.15)
        Path(command[-1]).write_text(json.dumps({"messages": []}))
        with lock:
            state["running"] -= 1

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(check.subprocess, "run", run)
    return state


def test_validations_are_bounded_and_the_heap_is_capped(fake_java):
    threads = [threading.Thread(target=check.epubcheck, args=(b"epub",)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(fake_java["commands"]) == 6 and fake_java["peak"] == 2
    assert all(command[:2] == ["java", "-Xmx1024m"] for command in fake_java["commands"])


def test_a_saturated_server_says_so_instead_of_piling_up(fake_java, monkeypatch):
    monkeypatch.setattr(check, "QUEUE_TIMEOUT", 0.05)
    monkeypatch.setattr(settings(), "epubcheck_concurrency", 1)
    busy = threading.Thread(target=check.epubcheck, args=(b"epub",))
    busy.start()
    time.sleep(0.05)
    with pytest.raises(ValueError, match="Trop de validations"):
        check.epubcheck(b"epub")
    busy.join()
    assert check.epubcheck(b"epub")["valid"] is True  # the slot was released


def test_without_a_jar_nothing_changes(monkeypatch):
    monkeypatch.setattr(settings(), "epubcheck_jar", "")
    assert check.epubcheck(b"epub")["available"] is False
