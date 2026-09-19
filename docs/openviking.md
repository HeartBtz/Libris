# OpenViking memory

This page is for administrators who want to connect Libris to an OpenViking server as an external,
semantic memory for their books. It explains what OpenViking adds, how to set it up, what Libris
writes there, what a passage is allowed to read back, and how to maintain it.

OpenViking is **optional**. Libris keeps every memory in its own database (PostgreSQL in production),
and the default `internal` backend reads it directly. OpenViking is an extra semantic index of the same
memories:

- everything written to it can be rebuilt from the database at any time;
- a failure of OpenViking never removes or undoes a result stored in Libris. With the `hybrid`
  backend, translation simply continues with the database alone, and the context inspector says so.

## Set it up

1. Run an OpenViking server that Libris can reach on your private network. Keep it private and
   maintained separately.
2. In **Settings › Memory · OpenViking** (administrators), fill in:

   | Field | Meaning |
   | --- | --- |
   | OpenViking URL | Base URL of the server. |
   | Dedicated `viking://` root | A dedicated sub-directory under `viking://resources/` (default `viking://resources/epub-translator`). The root itself is refused. |
   | API key | Sent as `X-API-Key`. Stored encrypted; never shown again. |
   | Authentication | **API key** (recommended), or **trusted** mode, which also sends the account and user headers you provide (`X-OpenViking-Account`, `X-OpenViking-User`). |
   | Context budget, retrieval budget | How much memory context, and how much of it from retrieval, a call may use (defaults 12000 and 6000). |
   | Minimum score | Relevance threshold of search hits (default 0.15). |
   | Timeout | Seconds per call (default 20). |
   | Semantic search (find), deep search (search) | Which OpenViking search modes Libris may use. |

   **Test connection** checks that the server answers, that the key is accepted and that the root can
   be searched. The first three settings can also come from `OPENVIKING_URL`, `OPENVIKING_API_KEY` and
   `OPENVIKING_ROOT_URI`; values saved in the interface win (see [configuration](configuration.md)).
3. Choose the memory backend per volume (the book's **Settings**) or as a series default:

   | Backend | Behaviour |
   | --- | --- |
   | `internal` | The database only. Nothing is written to OpenViking. |
   | `openviking` | Memories are mirrored to OpenViking and retrieved from it. |
   | `hybrid` | Both: retrieval uses OpenViking when it answers and the database otherwise. |

The automation API accepts the same values in `pipeline.context_backend` ([API guide](api.md)).

## What Libris writes

Libris writes with idempotent `replace` operations on stable URIs, without waiting for indexing. Every
identifier in a URI is a database id chosen by Libris, never a title or a path taken from a book:

```text
<root>/<owner_id>/series/<series_id>/volumes/<project_id>/events/<memory_id>.json   event, volume of a series
<root>/<owner_id>/series/<series_id>/volumes/<project_id>/book.md …                 catalog of that volume
<root>/<owner_id>/standalone/<project_id>/events/<memory_id>.json                   event, standalone volume
<root>/<owner_id>/standalone/<project_id>/book.md …                                 catalog of that volume
```

### Events

An event is one memory of the book: the analysis of a passage, its narrative state, or a validated
human decision. Its document is computed from the database at the moment it is written, so OpenViking
always holds the current event at its current place.

| Field | Meaning |
| --- | --- |
| `schema_version` | `2` |
| `owner_id`, `series_id`, `project_id`, `volume_number` | Where the memory belongs. |
| `chapter_id`, `chapter_position`, `chapter_number` | Its chapter, its order in the volume, and the author's number. |
| `segment_id`, `position` | The passage and its narrative position in the volume. |
| `type` | `analysis`, `narrative` or `human_decision`. |
| `identities` | Characters named by the memory (canonical names and who knows them). |
| `validated` | A person validated it. |
| `created_at` | When the memory was created. |
| `content` | The memory itself. |

The send queue only carries the work of writing a document; failed writes are retried with a growing
delay (about half an hour at most).

### Catalog documents

For each volume that has an analysis or a Book Bible, Libris also publishes a small named catalog,
refreshed every `MEMORY_CATALOG_INTERVAL_SECONDS` (60) when something changed:

| Document | Content |
| --- | --- |
| `book.md` | Title, author, languages, analysis progress and links to the other documents. |
| `book-bible.json` | The Book Bible (editorial synthesis), without the characters. |
| `characters.json` and `characters-NNNN.json` | Identities, aliases, role, description, gender, pronouns, speech style, and whether a person confirmed them. |
| `relationships.json` and `relationships-NNNN.json` | Relations between characters, with evidence, provenance and validation. |

These are compact projections; the database keeps the complete and exact data. Catalog documents are
**never** used as narrative evidence (see below).

## What a passage may read

A passage of volume N at position P searches with `target_uri` set to its series' `volumes`
directory, or to its own `events` directory for a standalone volume. That only narrows the search on
the server. Libris then keeps a hit only if:

1. its URI is exactly the URI of an event the database admits for this passage: a memory of the same
   volume at an earlier position (a person's validated analysis of the passage itself included), or a
   memory of an earlier volume of the same series, owner and language pair (volume number lower than
   N);
2. it is not a superseded human analysis, nor a human decision on a passage edited since;
3. the document read back is identical to the event computed from the database.

Everything else is rejected and listed in the context inspector with its reason
(`outside_narrative_allowlist`, `low_relevance`, `differs_from_canonical_event`,
`invalid_structured_memory`, `retrieval_budget`): later passages, later volumes, directory summaries,
catalogs, another owner's documents, stale or tampered documents. The Book Bible and character sheets
are editorial knowledge built from a whole reading, which is why they are never admitted as evidence
of what a passage may know. Continuous webnovel flows and unnumbered volumes have no "earlier volume":
they rely on their own chapters.

## Maintenance

On a **series** page, the **Memory** tab (**Series OpenViking memory**) shows, per volume, the events
waiting, written and failed, with the last errors, and offers:

- **Resynchronize**: retry now everything waiting, without waiting for the backoff delay;
- **Rebuild**: rewrite every event and catalog of the series from the database, including memories
  whose queue rows the retention already removed;
- **Reindex**: ask OpenViking to recompute its index of the series directory.

On a **volume**, the **Book Bible** tab has an **OpenViking memory** panel with the queue state, the
root, links that open the documents OpenViking actually holds (read through Libris, without exposing
the key), and these actions:

- **Synchronize the book and graph**: queue the catalog again (works even during an analysis);
- **Check in OpenViking**: read the catalog documents back and compare them with what Libris wrote,
  and look for them in the index (this confirms the documents found, not the indexing of every event);
- **Reindex in OpenViking** and **Rebuild from the database**, as for a series.

The same actions are available through the interface's API: `GET /api/series/{id}/memory`,
`POST /api/series/{id}/memory/{resync|rebuild|reindex}`, `GET /api/projects/{id}/memory/status`,
`POST /api/projects/{id}/memory/{synchronize|rebuild|reindex|check}`.

Libris never deletes anything in OpenViking. Deleting a book or a series, or moving a volume, leaves
the old documents in place; the database check simply no longer admits them. Clean them up with
OpenViking's own tools if you need the space.

When a volume moves into or out of a series, or is renumbered, its events and catalog are written
again at the new place automatically; until then, its remote hits are rejected and translation relies
on the database. The same automatic rewrite moves volumes written with the older
`<root>/<owner_id>/<project_id>/…` layout: no manual step is needed after an upgrade.

## Retention

`RETENTION_OUTBOX_SENT_DAYS` (7) deletes queue rows that were already written. This affects neither
retrieval, which is checked against the database, nor a rebuild, which recreates the rows it needs.
See [data retention](configuration.md#data-retention).
