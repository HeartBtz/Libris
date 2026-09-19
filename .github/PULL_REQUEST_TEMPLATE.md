## Problem

<!-- What is wrong or missing, and for whom? Link the issue if there is one. -->

## Change

<!-- What this merge request changes, in a few sentences. -->

## Verification

- [ ] Backend: `ruff check app tests` and `pytest -q`
- [ ] PostgreSQL run, if queries, locking or migrations changed
- [ ] Frontend: `npm run build` and the mocked Playwright specs, if the interface changed
- [ ] `python3 scripts/check_version.py`
- [ ] Documentation updated for any changed setting, endpoint or visible label
- [ ] No private data, book, credential or `.env` included

## Screenshots, migration or configuration impact

<!-- Screenshots of visible changes; new migrations; new or changed settings. Write "none" if not applicable. -->
