# OpenViking external memory

Libris works without OpenViking: SQL (PostgreSQL in production) is the source of truth for every
memory, and the `internal` context backend reads it directly. OpenViking is an optional semantic index
of the same memories. Everything written to it can be rebuilt from PostgreSQL at any time, and a
failure of OpenViking never removes or undoes a result stored in SQL: the `hybrid` backend then keeps
translating with SQL alone and says so in the context inspector.

Configure the instance in **Settings → Memory · OpenViking** (or `OPENVIKING_URL`, `OPENVIKING_API_KEY`,
`OPENVIKING_ROOT_URI`), then choose `openviking` or `hybrid` per volume or as a series default.

## Layout (since 0.6)

All identifiers in a URI are SQL UUIDs chosen by Libris, never a title or a path taken from a book.

```text
<root>/<owner_id>/series/<series_id>/volumes/<project_id>/events/<memory_id>.json   volume of a series
<root>/<owner_id>/series/<series_id>/volumes/<project_id>/book.md, book-bible.json…  catalog of that volume
<root>/<owner_id>/standalone/<project_id>/events/<memory_id>.json                   standalone volume
```

Before 0.6 a book used `<root>/<owner_id>/<project_id>/…` and events were named after rows of the send
queue. See *Upgrading* below.

## Event documents

An event is one SQL memory (`memories` row: analysis of a passage, narrative state, validated human
decision). Its document is a pure function of SQL:

| Field | Meaning |
| --- | --- |
| `schema_version` | `2` |
| `owner_id`, `series_id`, `project_id`, `volume_number` | where the memory belongs |
| `chapter_id`, `chapter_position`, `chapter_number` | its chapter, order inside the volume and the author's number |
| `segment_id`, `position` | the passage and its narrative position in the volume |
| `type` | `analysis`, `narrative` or `human_decision` |
| `identities` | characters named by the memory (canonical names, `known_by`) |
| `validated` | a person validated it |
| `created_at` | creation time of the SQL memory |
| `content` | the memory itself |

The send queue (`memory_outbox`) only carries the work of writing a document; the document is computed
again from SQL at the moment it is written, so what OpenViking holds is always the current canonical
event at its current place.

## What a passage may read

A passage of volume N at position P searches with `target_uri` set to its series' `volumes` directory
(its own `events` directory for a standalone volume). The server-side scope only narrows the search: a
second barrier in Libris keeps a hit only if

1. its URI is exactly the URI of an event SQL admits for this passage: a memory of the same volume at an
   earlier position (a person's validated analysis of the passage itself included), or any memory of an
   earlier volume of the same series, owner and language pair (volume number lower than N);
2. superseded human analyses and human decisions on a passage edited since are excluded;
3. the document read back (L2) is identical to the canonical SQL event.

Everything else is rejected and listed in the context inspector (`outside_narrative_allowlist`,
`low_relevance`, `differs_from_canonical_event`, `invalid_structured_memory`, `retrieval_budget`): later
passages, later volumes, directory summaries, catalogs, another owner's documents, stale or tampered
documents. The Series Bible and the Book Bibles are editorial knowledge built from whole volumes: they
are mirrored as named catalog documents but are never admitted as narrative evidence. Continuous
webnovel containers and unnumbered volumes have no "earlier volume": they rely on their own chapters.

## Maintenance

On a series page (**Mémoire OpenViking** tab) and on a volume (**Book Bible → Mémoire OpenViking**):

- **Backlog**: events waiting to be written, written, and failed (with the last errors), per volume.
- **Resync** retries now everything waiting, without waiting for the back-off delay.
- **Rebuild** rewrites every event and catalog of the series (or volume) from SQL, including memories
  whose queue rows the retention already removed. Writes are idempotent (`replace` on a stable URI).
- **Reindex** asks OpenViking to recompute its index of the series (or volume) directory.

API: `GET /api/series/{id}/memory`, `POST /api/series/{id}/memory/{resync|rebuild|reindex}`,
`GET /api/projects/{id}/memory/status`, `POST /api/projects/{id}/memory/{synchronize|rebuild|reindex|check}`.

Libris never deletes anything in OpenViking automatically. Deleting a book or a series, or moving a
volume, leaves the old remote documents in place; they are no longer admitted by the SQL barrier. Clean
them up with OpenViking's own tools if you need the space.

## Upgrading from 0.5

No manual step is needed. The first time the worker runs with OpenViking configured after the upgrade,
every volume using `openviking` or `hybrid` has its events written again from SQL into the new layout
(an `openviking_layout` marker in `app_settings` records that it was done); its catalog is written again
too. Until a volume's events are rewritten, its remote hits are rejected and translation relies on SQL
memory. The same replay happens automatically whenever a volume moves into or out of a series or is
renumbered.

## Retention

`RETENTION_OUTBOX_SENT_DAYS` deletes queue rows already written. This does not affect retrieval, which
is checked against the SQL memories, nor a rebuild, which recreates the rows it needs.
