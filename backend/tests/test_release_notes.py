import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

Intro.

## [Unreleased]

### Fixed

- **Pending.** Not released yet.

## [0.5.0] - 2026-10-01

Headline of 0.5.0.

### Added

- **Rollback.** A manual job (#56).

## [0.4.1] - 2026-09-18

### Fixed

- **Older.** Fix.

[0.4.1]: https://github.com/HeartBtz/Libris/releases/tag/v0.4.1
[0.5.0]: https://github.com/HeartBtz/Libris/releases/tag/v0.5.0
"""


def test_only_the_tagged_section_is_published():
    notes = release_notes.section(CHANGELOG, "0.5.0")
    assert notes == "Headline of 0.5.0.\n\n### Added\n\n- **Rollback.** A manual job (#56).\n"


def test_the_oldest_section_stops_before_the_link_definitions():
    assert release_notes.section(CHANGELOG, "0.4.1") == "### Fixed\n\n- **Older.** Fix.\n"


@pytest.mark.parametrize("version", ["0.5", "0.6.0", "0.5.00"])
def test_a_missing_section_is_an_error(version):
    with pytest.raises(ValueError, match=version):
        release_notes.section(CHANGELOG, version)


def test_the_repository_changelog_has_a_section_for_the_current_version():
    from app import __version__

    assert release_notes.section((ROOT / "CHANGELOG.md").read_text(), __version__).strip()


ENVIRON = {"CI_API_V4_URL": "https://git.test/api/v4", "CI_PROJECT_ID": "37", "CI_JOB_TOKEN": "token"}


def test_a_new_release_is_created():
    calls = []

    def call(method, url, token, payload):
        calls.append((method, url, payload))
        return 201, {}

    assert release_notes.publish("v0.5.0", "notes\n", ENVIRON, call) == "created"
    assert calls == [
        (
            "POST",
            "https://git.test/api/v4/projects/37/releases",
            {"tag_name": "v0.5.0", "name": "Libris v0.5.0", "description": "notes\n"},
        )
    ]


def test_a_rerun_updates_the_existing_release_instead_of_failing():
    answers = iter([(409, {"message": "Release already exists"}), (200, {})])
    calls = []

    def call(method, url, token, payload):
        calls.append((method, url))
        return next(answers)

    assert release_notes.publish("v0.5.0", "notes\n", ENVIRON, call) == "updated"
    assert calls[1] == ("PUT", "https://git.test/api/v4/projects/37/releases/v0.5.0")


def test_other_api_errors_fail_the_job():
    with pytest.raises(RuntimeError, match="HTTP 403"):
        release_notes.publish("v0.5.0", "notes\n", ENVIRON, lambda *_: (403, {"message": "forbidden"}))
