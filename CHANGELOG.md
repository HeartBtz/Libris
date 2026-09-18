# Changelog

All notable changes are documented here. Libris follows [Semantic Versioning](https://semver.org/); while the project is below 1.0, minor versions may include breaking operational changes that are called out explicitly.

## [0.5.0] - 2026-09-18

A new interface and the remaining findings of the September audit: lower model costs, several passages of a book translated at once, a faithful project archive, scheduled backups and a simpler release pipeline.

**Upgrading.** Five database migrations run automatically at start-up (`alembic upgrade head` in the Docker deployment); on a large production database they take a minute or two, mostly to fill the translation-memory key of existing passages. Behaviour changes to know about:

- The worker now purges diagnostic data every hour: prompts and raw answers of model requests older than 30 days (the requests, their tokens and costs stay), progress events older than 7 days except the last 500 per book, sent memory-outbox rows, automatic Book Bible revisions beyond 20 per book, and the per-passage state of jobs finished more than 30 days ago. Every rule has a `RETENTION_*` setting; `0` disables it. To give the freed space back to the system, run `VACUUM (FULL, ANALYZE) llm_requests;` once with the worker stopped.
- A provider now serves several passages of the same book at once, up to its `max_concurrency`; set `WORKER_BOOK_PARALLELISM=1` to keep one passage at a time.
- Identical passages already translated in your books are reused without a model call (translation memory, on by default per book).
- `/openapi.json` requires a session; `GET /metrics` stays disabled until `METRICS_TOKEN` is set.
- Uploads are refused before being read when they exceed `MAX_UPLOAD_MB` (or 1 MiB without a session).
- Behind a reverse proxy, keep `FORWARDED_ALLOW_IPS` set (see 0.4.1).

### Security

- **Resource exhaustion by a signed-in account.** Live progress streams were unbounded and queried the database inside the API's event loop; an EPUB made of many small, highly compressed files passed the compression check (319 KiB unpacked to 285 MiB); every chapter preview rebuilt and unpacked the whole book. Live streams are now limited per account (`EVENT_STREAMS_PER_USER`, 4) and per process (`EVENT_STREAMS_TOTAL`, 100) and poll the database from a worker thread; the declared unpacked size and the whole-archive compression ratio (`MAX_COMPRESSION_RATIO`, 100) are checked before an EPUB is unpacked; previews reuse a bounded cache (`PREVIEW_CACHE_MB`, 64) that any edit invalidates (#52).
- **API description and provider addresses.** `/openapi.json` was public; it now requires a session and can be removed with `OPENAPI_ENABLED=false`. Non-administrators no longer see providers' addresses, only their name and model. Changing the address or type of a provider that holds an API key now requires entering the key again, so a stored key cannot be redirected to another host (#52).
- **Web search client.** The SearXNG client followed the server's proxy environment variables and read answers of any size; it now ignores them, like every other outbound client, and stops reading beyond 2 MiB (#52).
- **Series conventions.** An editor of a shared book could give it the name of one of the owner's private series and pull that series' terms and translation decisions into the book's prompts. Only the owner may now attach a book to a series that contains books the editor cannot read (#52).

### Added

- **Prometheus metrics.** Nothing told an operator from outside that the worker had stopped taking jobs, that a job's lease had expired, or how many tokens each step of the pipeline burns: the cost analysis of the production instance had to be done by hand in SQL. `GET /metrics` now exposes, in the Prometheus text format, jobs by operation and state, the age of the oldest job waiting for a worker, expired leases, model requests by operation and outcome, input, output and wasted tokens by operation, cache hits, requests in flight per provider (its name, never its address), passages by state and the external-memory backlog. Counters are read from the database, so they are cumulative and survive restarts; no label carries a book title, text or URL. The endpoint is disabled (404) until `METRICS_TOKEN` is set (24 characters at least), then requires it as a Bearer token; results are cached for 10 seconds. The operations guide has a scrape configuration and alert examples (#61).
- **Cost estimate before starting a job.** A book costs tens of millions of input tokens, and the first figure came after the work was done. `GET /api/projects/{id}/estimate?operation=analyze|translate|review` returns the passages still to process, the expected requests, input and output tokens and the cost at the provider's current prices, without calling any model. Passages already analysed, finished, corrected by hand or validated are not counted. The figures come from the owner's previous books on the same provider when at least 5 passages were processed (retries and errors included), otherwise from documented defaults; `basis` says which (#61).
- **Wasted tokens.** The cost of a book counted input tokens spent on requests that failed validation, were refused or interrupted as if they were useful work — about 12 % of the production spending. `GET /api/projects/{id}/metrics` and, per model, `GET /api/statistics/models` now also report `wasted_input_tokens` and `wasted_share`; existing fields are unchanged (#61).
- **Glossary exchange with translation tools.** Glossaries could only be exchanged as Libris JSON or as a comma-separated CSV with English column names, so a termbase from a CAT tool or a CSV saved by a French spreadsheet could not be imported. The glossary now also exports to TBX (TBX v3 with TBX-Basic data categories: subject field, definition, one language section per book language; locked, accepted and proposed terms travel as the standard `preferred`, `admitted` and `deprecated` statuses), and the import recognises JSON, CSV or TBX by extension or content. TBX v3 and older TBX v2 (`martif`) termbases are read with the same safe XML reader as EPUB files. CSV files may use `;`, `,` or tabs, UTF-8 with or without BOM, UTF-16 or Windows-1252, common French or English headers ("Terme source;Traduction", "Source term", "Target"…), yes/no values in both languages, or no header at all; the quote added on export to protect terms such as "-kun" from spreadsheet formulas is removed again. An invalid file is refused with the line or term at fault (#52).
- **Error messages in English.** With the interface in English, the API still answered its errors in French. Error messages are now translated when the request's `Accept-Language` prefers English, including messages that carry a value (book titles, counts, HTTP statuses), input validation messages and EPUBCheck report summaries; French remains the default. A test fails whenever a message raised in the code has no English version (#52).
- **Several passages of a book at once.** A book was translated one passage at a time, so a provider able to serve four requests worked at a quarter of its capacity whenever a single book was running. Translation, the final review and the consistency checks now keep several passages of a book in flight, up to the provider's `max_concurrency`, shared fairly between the books using it at the same time; model calls wait their turn per provider in arrival order. With a fixed-latency mock and a capacity of 4, a book goes from 3.7 to 13.2 passages per second in translation and from 3.7 to 14.0 in final review. A passage's context shows the translation of the neighbours already done and only the source of those still in flight; chapter analysis and the Book Bible synthesis stay sequential because each step builds on the previous one. A pause, a cancellation or an outage stops every call in flight, and a resumed job redoes only the interrupted passages. The new `WORKER_BOOK_PARALLELISM` setting caps the number per book (`0`, the default, follows the provider; `1` restores one passage at a time) (#51).
- **Translation memory**: every passage cost a model call even when you had already translated exactly the same text (the title repeated in each document, identical interludes, recurring passages across a series). A passage whose source is identical (Unicode forms and spacing aside, formatting included) to a finished passage in one of your books with the same language pair now reuses that translation without a model call, preferring your validated versions; in a series only the same book and earlier volumes are used. The version is labelled `translation_memory`, and review and final review still apply. The **Translation memory** setting (on by default, `translation_memory` in the project settings API) and the number of reused passages appear in the book strategy panel. On a test book with five identical interludes, 14 of 22 passages were reused. (#54)
- **Right-to-left export**: a book translated into Arabic, Hebrew, Persian or Urdu kept a left-to-right layout. Exported documents now get `dir="rtl"`, EPUB 3 spines turn pages right to left, contrary inherited `dir` attributes are adapted, and elements that declared the source language no longer announce it over the translated text (quotations in a third language keep theirs). (#54)
- **Book blurb translated**: `dc:description` (and short `dc:subject` entries) stayed in the source language in the exported book. They now form a "Book metadata" section, translated like any passage. (#54)
- **Unsaved translations are protected.** Switching tab, section, page or book, or closing the browser tab, with an edited but unsaved passage used to discard it silently. Libris now asks before leaving, and text typed while a save is in flight stays as a draft instead of being overwritten. `Ctrl`/`⌘`+`S` saves the passage and `Ctrl`/`⌘`+`Enter` validates it. (#50)
- **Cost estimate before paid operations.** Starting an analysis, a translation or the AI review happened on a single click with no idea of its cost. A confirmation now shows the server's token and cost estimate when it can provide one. (#50)
- **Screens for existing features.** Project members can now be listed and revoked, a provider can be deleted (the server still refuses while it is in use and explains why), per-passage guidance can be edited, the book memory can be reindexed or rebuilt, the translation memory setting can be switched per book, and the glossary imports and exports JSON, CSV and TBX. Characters are edited in a form instead of raw JSON. (#50)
- **Full journey test.** A `@journey` Playwright spec imports an EPUB, registers the mock provider, lets analysis and translation run on the real worker, validates a passage and exports the EPUB. (#50)
- **Rollback.** When a release migrated fine but misbehaved, there was no quick way back: the deployment procedure refused any older version and the guide said to rebuild the previous tag. A manual, protected `rollback-production` job now redeploys the previous version, whose images every deployment keeps on the host, through the same guarded procedure (dump, health check, worker restart from its checkpoints). It refuses before touching anything when the database schema changed since that version, and points to the dump restoration instead (`libris-production-deploy --rollback`, see *Rollback* in the release guide) (#56).
- **Scheduled backups.** The only backups were the pre-deployment dumps, kept on the production disk and without the books: losing that disk lost every book and translation. `deploy/libris-backup` and its daily systemd timer dump the database while Libris keeps running, archive the books volume, read both back before the backup counts, write checksums to a share of another host, refuse to write when that share is not mounted, and rotate old backups only after a verified one. `deploy/libris-restore` restores a backup into a separate throwaway Compose project (never the worker) and checks migrations and health, for a monthly restore test; `--dry-run` checks a backup without Docker. See `docs/backup.md`; nothing is installed automatically (#56).
- **User journey in CI.** No automated test ever clicked through import, analysis, translation, validation and export against a real backend. The `e2e` job starts the real Compose stack with the synthetic model of `docker-compose.test.yml` and runs the Playwright specs tagged `@journey`; the report, traces, screenshots and service logs are kept when it fails (#56).
- **EPUBCheck in the tests.** The test suite never ran the EPUB validator that the image ships, so an export EPUBCheck rejects was only discovered by users. Tests marked `epubcheck` now export the reference book, an EPUB 2 with `&nbsp;` entities and a translated table of contents and validate them with the real EPUBCheck 5.3.0 inside the built image (skipped elsewhere unless `EPUBCHECK_JAR` is set) (#56).

### Changed

- **Lighter project list.** The library polls the project list every five seconds, and each book cost 12 to 16 database queries plus its whole Book Bible: about 700 queries per tick and per open tab for 50 books. The list is now computed in a constant number of queries whatever the number of books (8 books: 99 queries before, 10 now) and no longer includes the Book Bible, which the book's own page still loads; every other field keeps its value (#52).
- **Job checkpoints (migration).** Each job kept, in its checkpoint, a list of every passage it had finished or reviewed — with the outcome of each review and the repaired batches —, rewritten at every step and sent back with every job listing: up to 443 KB per job in production, and several megabytes on a very long book. That state now lives in a dedicated table, and a checkpoint only holds a cursor and counters (a few hundred bytes, whatever the book). The migration converts existing jobs in place: a paused job resumes without retranslating anything and past review results keep showing; `alembic downgrade` restores the former format. Project archives carry this state with their jobs, and an archive exported earlier is converted when it is restored (#51).
- **Data retention: state of finished jobs.** The per-passage state of a job (see *Job checkpoints*) would have been kept forever. Once a job has been completed, failed or cancelled for more than 30 days (`RETENTION_JOB_STATE_DAYS`, `0` disables), the worker's hourly clean-up deletes what only served to resume it and keeps the final review outcomes shown in the book's review history; paused or waiting jobs are never touched. `python -m app.maintenance.retention --dry-run` reports it (#51).
- **Prompts resist text that pretends to be instructions**: a book quoting `</TARGET_TEXT>` could close a prompt section and speak with the prompt's authority. Section contents are now escaped, every prompt (administrator overrides included) states that book text and context are untrusted data, languages are named ("French (fr)") instead of raw codes, writing and review prompts carry a tu/vous consistency rule and the target language's typography (French no-break spaces, « », dialogue dashes), the marker instruction matches what validation accepts, revision and final review no longer disagree on uncertainties, and the JSON schema is sent once instead of twice. Prompt versions (`file-v2`, `db-vN`, `+rules-v1`) show the change in the inspector. (#54)
- **Small context windows**: with an 8k or 16k provider every passage failed with advice to shorten the instructions, whatever they were. The budget now reserves the actual response schema instead of a fixed 6,000 tokens, the error gives the real figures (window, output reservation, prompt, passage, rules) and the window that would fit, and on a small window a long passage is translated in parts cut at sentence boundaries, then reassembled. The context inspector's input estimate is now exactly what is checked against the window. (#54)
- **Redesigned interface.** The navy and coral interface mixed fonts, boxed every element and gave every button the same weight. Libris now has a collapsible sidebar (a drawer on phones), a warm light theme and a neutral dark theme that follow the system by default, one indigo accent, Inter for the interface and a book serif for the text being translated, reusable components and design tokens. The library offers a segmented filter, a dense table or card view and drag-and-drop import; a book shows one contextual main action, a slim five-step stepper and tabs; the review queue shows source, translation, AI doubts and proposals side by side with Accept, Edit and Reject. (#50)
- **Formatting codes are no longer shown raw.** The editor printed `⟦t0⟧…⟦/t0⟧` codes as-is. The source text now shows the book's formatting, and in the translation field the codes appear as discreet chips; the text sent to the server is unchanged, and a warning appears when the codes no longer match the source. (#50)
- **Paginated review queue.** The validation screen loaded and rendered every flagged passage at once; it now loads twenty at a time. (#50)
- **Localised interface text.** Plurals follow each language's rules instead of "(s)", numbers, percentages and dates follow the interface language, a few remaining hard-coded French strings went through the translation catalogue, conflicting English translations of the same text were unified, and every API request sends `Accept-Language` so server errors come back in the interface language. (#50)
- **Accessibility.** Native `prompt()`, `confirm()` and `alert()` dialogs are replaced by accessible dialogs with focus management; links of the character graph are listed as focusable buttons and announced to screen readers; focus is visible everywhere, phone touch targets are at least 40 px and animations respect reduced motion. (#50)
- **Faster, leaner pipeline.** Release tags re-ran every test already passed on the default branch, pip and npm downloaded everything at every job, and a documentation-only merge request tested and built like a code change. Tags now promote the images the default branch verified, without re-testing (about 2 minutes less before the deployment starts); dependencies are cached per lockfile; the frontend job installs once; Trivy analyses each image once; a documentation-only merge request only runs the version, dependency and secret checks. Playwright specs are selected by tag (`@integration`, `@journey`), so the ten mocked specs all run instead of a hand-maintained list of eight (#56).
- **Release notes.** The GitLab release showed the whole CHANGELOG since 0.1.0, and re-running the release job failed because the release already existed. Releases on GitLab and GitHub now contain only the section of the tagged version, the tag pipeline fails early when that section is missing, and a re-run updates the release (#56).
- **Production disk.** Every deployment left its application and Codex images behind on the production host (4.7 GB found). A successful deployment now removes the Libris images that are neither deployed nor kept for rollback, and the untagged pulls of the Libris registry; no other image or volume is touched. `libris-production-deploy --prune-images --dry-run` shows what would go (#56).
- **Migration checks.** Downgrades were only tested down to one intermediate revision, and GitLab never compared the models with the migrations. Both CIs now migrate PostgreSQL up, all the way down to an empty schema, up again, and run `alembic check`; the SQLite test does the full round trip too (#56).

### Fixed

- **Release pipeline.** The production deployment only waited for the release images to be tagged, so it started while the container runtime test and the vulnerability scan were still running: a failed gate could not stop it. It now waits for the runtime test, the scan and the GitLab publication, and a test keeps those gates in place. The release guides describe the deployment as automatic, consistently (#30).
- **Locked glossary.** A correct translation was rejected when it differed from the locked term only by typography or grammar — a curly or straight apostrophe, a no-break space, unaccented capitals, a simple plural, or a contracted article ("du Conseil" for "le Conseil") — and a capitalised term such as "Will" was triggered by the ordinary word "will". Each false alarm cost up to thirteen model calls and left the passage in error. The check now ignores those differences, still reports a term that is really missing, and its message names the expected terms (#31).
- **Invalid model answers.** An answer refused by validation (missing or merged paragraphs, invalid JSON, locked glossary) was asked again unchanged, up to five times at full price. The next attempt now tells the model why its answer was rejected, and validation failures stop after three attempts so that the cheaper small-batch repair takes over; network errors keep their five attempts (#32).
- **Context allocation.** The two passages before and after the one being translated took the whole optional context allowance on their own, so validated character sheets, accepted glossary terms and the chapter state were silently left out of every request, whatever the model's window. The neighbourhood now gets at most 60 % of that allowance when other material competes for it, and a neighbour that is too long is shortened to an excerpt instead of being dropped (which could end in a misleading "window too small" error). The default allowance, hence the cost of a request, is unchanged; the Context Inspector's input estimate is now correct (#33).
- **EPUB import.** Two kinds of ordinary books could not be imported. EPUB 2 files using named HTML entities (`&nbsp;`, `&eacute;`, `&hellip;`…) were refused with "Entité XML non résolue": these entities are now converted from their fixed table, while entity declarations and external DTDs remain refused. A manifest entry whose file is missing from the archive (a deleted font or image) caused an HTTP 500: title, author and language are now read from the package already parsed, the book imports, and the dangling entry is dropped from the exported EPUB; a missing document of the reading order is refused with its file name (#34).
- **Table of contents.** A translation — typed by hand or returned by the model — could add text next to the link of a contents entry (`<li><a>…</a> extra</li>`). Nothing objected until the final export, which then failed with a raw EPUBCheck message (`RSC-005`) that did not point to the passage. Such a translation is now refused when it is saved, with an explicit message, and the model is told why. Page lists (`page-list`, NCX `pageList`) are no longer split into passages, so newly imported books stop paying model calls to "translate" page numbers; books already imported are unaffected (#35).
- **Resuming a job.** Every time a job resumed — after a pause, a worker restart or a provider outage — it rewrote its checkpoint and emitted a progress event for each passage already done, at every stage: thousands of database transactions and events per resume on a long book (one production book reached 86,000 events). Passages already settled are now set aside in one query before the loop, so a resume only writes for the work that remains; progress numbering still spans the whole book (#36).
- **Worker heartbeat.** A single database error while renewing a job's lease — a brief PostgreSQL restart, a saturated connection pool — cancelled the model call in progress, often a long one already paid for, which was then made again. The lease lasts 60 seconds, so the heartbeat now keeps trying and only gives up after 40 seconds without a successful renewal; a pause, a cancellation or a lost lease still stop the job at once (#37).
- **Database indexes.** Several queries that run in a loop had no suitable index: the count of a provider's running calls taken before *every* model call, the look-up of a passage's analysis memory, the event feed polled by each open browser tab, the memory outbox scan, and the foreign keys followed when a book is deleted. A migration adds them (`alembic upgrade head`, applied automatically by the Docker deployment; it takes a few seconds even on a large database) (#38).
- **Upload size (security).** File uploads were written to the server's temporary directory in full before the session was checked and before `MAX_UPLOAD_MB` was applied: anyone able to reach Libris could fill the disk without an account. Requests are now bounded before anything is buffered — 1 MiB without a session cookie (answered `401`), `MAX_UPLOAD_MB` plus a small margin with one (answered `413`) — from `Content-Length` when it is declared, and while reading for chunked transfers (#39).
- **Exports without the source file.** When a book's original EPUB could not be found on the server — a data directory restored under another path, a deleted file — *every* export failed with an HTTP 500, including plain text, Markdown and the Book Bible, which do not need it. The file is now also looked up at its deterministic place under `DATA_DIR/books`, the text exports and the Book Bible keep working without it, and the EPUB, project archive and preview answer with an explicit message (#40).
- **Documentation.** The installation guides still pinned `heartbtz/libris:0.3.1`; they now follow the current release and `scripts/check_version.py` refuses a release whose guides pin another version. A "JSON" export was advertised that does not exist (the JSON export is the Book Bible), the native Anthropic and OpenAI providers were missing from the README, the provider retry delays (60 s to 1 h) and the worker heartbeat (2 s) were misstated, six settings were undocumented (`SESSION_DURATION_HOURS`, `FORWARDED_ALLOW_IPS`, `WORKER_HEARTBEAT_SECONDS`, `MEMORY_CATALOG_INTERVAL_SECONDS`, `PROVIDER_RECOVERY_BASE_SECONDS`, `PROVIDER_RECOVERY_MAX_SECONDS`), and `.env.example` now warns that `COOKIE_SECURE=true` breaks logins over plain HTTP (#41).
- **Data retention (behaviour change).** Nothing was ever purged: on the production instance the request log had reached 8.3 GB of a 8.9 GB database, with 612,000 progress events. The worker now cleans up at start-up and every hour: request logs older than 30 days lose their prompt, raw response and context trace — the row, its token counts, cost, duration, error and cached answer are kept, so statistics and the request cache are unaffected —, progress events older than 7 days are deleted except the last 500 of each book, as are memory-outbox rows sent more than 7 days ago and automatic Book Bible revisions beyond the last 20 per book (human revisions are all kept). Each rule has a setting (`RETENTION_REQUEST_BODIES_DAYS`, `RETENTION_EVENTS_DAYS`, `RETENTION_OUTBOX_SENT_DAYS`, `RETENTION_BIBLE_REVISIONS`; `0` disables it) and `python -m app.maintenance.retention --dry-run` reports what would go. A cache hit no longer stores a second copy of the whole prompt. See the operations guide for reclaiming disk space (#42).
- **Jobs without a provider.** A job whose provider had been deleted, or that was created before any provider was chosen, stayed "pending" forever without a word, and kept the book locked against any other work. It is now marked as blocked with an explicit message — choose a provider for the book, then resume the job — and resuming starts it normally (#43).
- **Summary & recovery.** The completion report loaded every passage of the book — units, translations, critiques — just to count them, and it is refreshed with every burst of job events: about 1 MB and 0.2 s per call on a 2,300-passage book. The counts now come from the database, and the list of passages to recover reads only the columns it shows and is limited to the first 500, with the total displayed when there are more (#44).
- **Cost statistics.** The cost of a book was recomputed with the provider's *current* prices, so changing a price — or fixing a typo in one — silently rewrote the cost of every book already translated; requests of a deleted provider were left out, and the per-model statistics attributed a provider's whole history to its current model. Each request now records the prices in force (migration adding two columns, instantaneous), the cost uses them and only falls back to the current price for older requests, requests of a deleted provider are counted, and the per-model statistics use the model recorded with each request (#45).
- **XML entity declarations (security).** Libris refuses EPUB files that declare XML entities, but it looked for the declaration as ASCII bytes: a file encoded in UTF-16 went through, and entities used inside an attribute value were expanded. The check is now made on the parsed document type, whatever the encoding. External entities and network access were, and remain, disabled (#46).
- **Changed or lost `SECRET_KEY`.** Provider API keys are encrypted with `SECRET_KEY`. After a restart with another key — a database restored without its `.env`, a rotation — every model call failed as an "invalid answer", was retried, and passages ended in error without any explanation; the provider test showed `InvalidToken`. Libris now detects an unreadable stored key before calling the provider, puts the job on hold like an authentication failure, and tells you to enter the key again. An unreadable OpenViking key only disables the external memory, with a warning in the logs (#47).
- **Memory relevance.** Memories recalled for a passage were ranked by the words they share with the retrieval query — which begins with an English instruction meant for the semantic search service. Words such as "relationships", "objects" or "known" therefore favoured the same memories whatever the passage said. The local ranking now only compares with what comes from the book (entities, neighbouring text, the passage, specific needs); the query sent to an external memory service is unchanged (#48).
- **EPUBCheck load.** Every import and export starts a Java process to validate the EPUB, for up to 90 seconds, with no memory ceiling and no limit on how many run at once: a few simultaneous exports could take the host's memory and processors away from the API and the worker. At most two validations now run at a time per process (`EPUBCHECK_CONCURRENCY`), each capped at 1 GB of heap (`EPUBCHECK_MAX_HEAP_MB`); the others wait their turn, and a saturated server answers with an explicit "try again" message (#49).
- **Project archive.** Restoring a project archive lost the work it was meant to protect: validated passages came back "to check", critiques, uncertainties, quality issues, jobs, Book Bible revisions and request statistics were gone, the version history was doubled, and an archive without its project section answered HTTP 500. Archives now use format version 2, which carries the whole state of the book and is validated before anything is written: an incomplete or altered archive is refused with the faulty fields named, never with a 500. Owners, members, permissions and the provider are deliberately not restored — the person restoring becomes the owner and chooses a provider — and a job that was running comes back paused. Version 1 archives remain readable. The export now refuses an archive that the import could not read back (`MAX_UPLOAD_MB`, `MAX_UNPACKED_MB`) and says which setting to raise, and a restore no longer blocks the API while it runs (#52).
- **A large book slowing down the others.** The worker ran its database queries and book-sized computations (context building and memory scoring, consistency sampling, logging of model calls) directly in the loop shared by all its jobs: with two books of 1,500 passages, the loop stalled for up to 450 ms at every passage, delaying the lease renewals of every other book. That work now runs in background threads, lease renewals have their own, and memory scoring no longer re-reads the query for every memory: the worst stall drops to about 100 ms and leases are renewed on time (#51).
- **Edits while the worker writes (PostgreSQL).** Saving a human correction, or keeping the original of a passage, while a job was writing that same passage could end in a database deadlock, and one of the two operations failed with an error. The API now takes its locks in the same order as the worker, so it waits a moment and, if the worker's version arrived first, reports the usual "modified meanwhile" conflict (#51).
- **Series: an earlier volume's locked term no longer changes in a later one**: an unlocked term of volume 2 overrode a term locked in volume 1, locked series terms were never checked in the output, and "en"/"en-US" or "Saga"/"saga" silently separated volumes. Locked series terms now win and are checked like the book's own locked glossary, series and languages are matched after normalisation, and human corrections validated in earlier volumes reach later ones as short before/after choices instead of whole passages. Nothing from later volumes is ever used. (#54)
- **Long Japanese, Chinese and Korean paragraphs are split**: a 9,000-character paragraph was never cut because sentence boundaries required a following space, and exceeded the model's output. Paragraphs are now cut after 。！？… (closing quotes included). (#54)
- **Furigana stay furigana**: ruby readings (`rt`, `rp`) were translated along with the base text; they are now kept as they are. (#54)
- **No more text lost or scattered at import**: text mixed with block elements ("Dear <b>Alice</b>, … <div>signature</div>") became one fragment per text node, and text following a comment outside a paragraph was dropped. Each run of text between blocks is now one passage unit; SVG text and `aria-label` are translated; preformatted text, formulas and SVG titles are kept and listed in the import report instead of vanishing silently. (#54)
- **Section count excludes the table of contents**: `nav.xhtml` and `toc.ncx` were counted as chapters (8 sections for a 6-chapter book); their passages are still translated and shown. Pages marked `linear="no"` now come after the story instead of opening it. (#54)
- **"Accept all" applied every AI proposal on a single click.** It now asks for confirmation first. (#50)
- **Misleading empty states.** Screens showed "no books" or "nothing to review" while their data was still loading; they now show placeholders until the answer arrives. (#50)
- **Batch series numbering ignored the interface language** when sorting titles. (#50)
- **CI jobs leaving containers behind.** Cancelling a stuck job killed only the runner's `docker` client: the anonymous test container went on running (24 minutes seen), its throwaway PostgreSQL stayed up and nothing cleaned them. Every CI container is now named and labelled after its job and removed by the job's `after_script`, including on cancellation; test commands and jobs have explicit time limits so a hang fails within minutes, and containers older than two hours are reaped by the next pipeline (#56).
- **Release tag racing its own branch pipeline.** A tag pushed right after its merge could start before the default-branch pipeline had built, or finished testing, that commit's images, and failed on a missing image — or promoted an image whose checks were still running. The branch pipeline now marks a commit's images once every job has passed (`verified-sha-<commit>`), and the tag pipeline waits up to 20 minutes for that mark before promoting them (#56).

## [0.4.1] - 2026-09-18

Follow-up to 0.4.0: the remaining findings of the audit. No database migration and no configuration change is required; behind a reverse proxy, consider setting `FORWARDED_ALLOW_IPS` (see *Security*).

### Security

- **Login throttling.** The brute-force protection counted every login — successful ones included — in a single bucket per client address. Behind a reverse proxy all visitors share the proxy's address, so twenty logins in five minutes locked everybody out, and anyone could do it on purpose. Only failed attempts now count, per client address *and* account name (with a higher overall ceiling per address), a successful login clears the counter, and expired entries are purged. Set `FORWARDED_ALLOW_IPS` to your proxy's address so that real client addresses are used (#22).

### Fixed

- **Edit conflicts in the editor.** When a running job delivered a new version of a passage while you were typing, every save was refused (HTTP 409) and nothing let you get out of it short of reloading the page. The notice now offers two explicit choices: reload the server version, or keep your text and save it over the latest version (#23).
- **No more out-of-date answers on screen.** Switching chapter, filter or page quickly while the server was slow could show the previous chapter's passages under the new chapter title, and overlapping refreshes could leave the book header in a past state. Only the most recent request now updates the screen. The completion report also follows the job's progress and no longer needs a manual "Actualiser le bilan" to re-enable "Relancer la sélection" (#24).
- **Data consistency.** Restoring a "source kept" version (or re-importing a project archive containing one) now brings back the dedicated "Original conservé" status, so the passage stays listed among those still needing a translation; conversely, replacing a kept original with a real translation settles its standing alert. Importing a glossary whose JSON root is an object (`{"terms": [...]}`) is refused with an explicit message where it used to answer "0 imported" and swallow the mistake. Saving a character sheet now refuses an empty or over-long name and a name or alias that already belongs to another confirmed character, like the dedicated alias action already did (#25).
- Database migrations now run on SQLite, the default `DATABASE_URL`: `alembic upgrade head` used to stop on the job/provider migration with "No support for ALTER of constraints in SQLite dialect". PostgreSQL installations are unaffected (#26).
- `WORKER_HEARTBEAT_SECONDS` and `MEMORY_CATALOG_INTERVAL_SECONDS` are now honoured by the worker (they were declared but ignored); out-of-range values are refused at start-up (#27).
- **Applying accepted suggestions.** The step that applies the AI suggestions you accepted kept a database connection open for the whole duration of each model call — minutes with a slow provider — which could exhaust the connection pool when several books were in that step. It now reads, calls the model, then writes, without holding anything in between; and a result that arrives after you paused or cancelled the job is no longer applied (#28).
- **"Retranslate" really asks the model again.** A forced retranslation of a passage whose context had not changed was silently answered from the request cache, so it returned the very same text without calling the provider. Forced jobs now bypass the cache lookup; the fresh answer is stored and reused as usual (#29).

### Changed

- Operations guide: `docs/ci-cd.md` now lists what lives outside Git on the production target and gives a verified procedure to re-provision `/opt/libris-production` if it is lost, with a warning to free disk space inside `backups/` and never by deleting the directory (#21).
- CI: the image build job now removes this project's own unused images older than three days from the shared runner (they remain in the registry); per-commit images had been accumulating there indefinitely (#20).
- Removed the unused "structured error codes" module and circuit-breaker helpers announced in 0.3.4: nothing ever called them and their retry tables contradicted the real behaviour, which is the one described under *Automatic provider recovery* in 0.4.0 (#27).

## [0.4.0] - 2026-09-18

A maintenance release coming out of a full audit of the application and of its production instance. It fixes the EPUB 3 export failures, makes the worker and provider recovery robust, stops two disk-space leaks, and repairs a number of interface defects. It is a minor version because a few behaviours change on purpose: sessions now last the documented 24 hours by default (`SESSION_DURATION_HOURS`), the live event stream of a book no longer replays its history, starting a job on a busy book answers HTTP 409, and "Configure selection" no longer overrides languages and qualities you did not set. No database migration is included.

### Fixed

- **EPUB 3 export compatibility.** Fully translated EPUB 3 books could not be exported (`HTTP 422`, "EPUBCheck signale un EPUB invalide") when the *source* file carried conversion artefacts from Kobo, Calibre or Sigil. Nine volumes of one series were affected in production while their EPUB 2 siblings exported fine, because only EPUB 2 packages were normalized before validation. Export now repairs these inherited defects deterministically, without touching the translated text, and EPUBCheck remains a blocking gate (#4):
  - `<script src>` stubs whose local target is missing from the archive (typically `js/kobo.js`) are removed (`RSC-007`);
  - the manifest `scripted` property is reconciled with what each content document really contains (`OPF-014`/`OPF-015`);
  - the NCX `dtb:uid` is realigned with the package unique identifier (`NCX-001`), for EPUB 2 and EPUB 3;
  - empty XHTML `<title>` elements receive the book title (`RSC-005`).
- **Readable export and API errors.** A refused EPUB export now tells you which book failed and lists the first EPUBCheck errors, in both single and bulk export; previously the interface showed only "Export refusé (HTTP 422)" or a raw JSON report. Form validation errors show the field and the reason without ever echoing the typed value back (the login screen could display the password just entered), and an HTML error page from a reverse proxy is reported as `HTTP 502/504` and no longer as a JSON parsing error (#5).
- **Worker stability.** An unexpected error in a single job could stop the whole worker and interrupt every book being translated — for example pausing a book while the provider was refusing a passage, or a provider sending a malformed `Retry-After: inf` header. Each job is now isolated: the failure is recorded on that job only and the other books keep going (#6).
- **Automatic provider recovery.** A provider answering with a tiny or fractional `Retry-After` (for example `0.5`) made the job retry immediately in a loop, and any `Retry-After` switched the exponential backoff off entirely; the backoff is now a minimum wait that a provider can only lengthen, and the random jitter no longer exceeds `PROVIDER_RECOVERY_MAX_SECONDS`. Temporary overloads reported as HTTP 529 (Anthropic "overloaded"), 520–524 (Cloudflare) or 425 now put the job in "waiting" with automatic resume; they used to fail it and require a manual restart. Waiting times also stop escalating after unrelated hiccups: the outage counter is reset by every successful provider call, not only at the end of a translated passage (#7).
- **Native Anthropic and OpenAI providers.** The two provider types announced in 0.3.4 could not actually be created (the API and the settings screen rejected them) and, once wired, would have translated without your instructions: only the last system message reached Claude, so the translation prompt was replaced by the JSON-format reminder. They now go through the same path as every other provider, which brings what was missing: the full system prompt, token usage and costs in the statistics, the stored raw response, detection of refusals (`stop_reason: refusal`) and truncated answers (`max_tokens`), the per-step temperature for OpenAI, and the configured output limit instead of a silent 8192 cap. For Claude, temperature and Top P are no longer sent because current Claude models reject them, and models are listed live from `/v1/models` in place of a hard-coded, outdated list. Both types accept a base URL with or without `/v1`, require an API key, and an empty `choices` answer is handled as a provider error and no longer crashes the job (#8).
- The "New provider" form no longer keeps the API key, model list and status message typed for the previously opened provider (#8).
- **Error messages stay on screen.** The red error banner used to vanish by itself — within five seconds in the library, within a second inside a book with a running job — because every automatic refresh cleared it. An export refusal or a save conflict could disappear before it was read. Automatic refreshes no longer erase or replace the error of an action; the banner stays until you dismiss it or start another action (#9).
- **Sessions.** `SESSION_DURATION_HOURS` was documented but ignored: sessions always lasted 12 hours. The setting is now applied to both the server-side session and the cookie (default 24 hours, as documented). When a session expires or is revoked, the interface returns to the login screen with an explicit message; it used to stay stuck on "Connexion nécessaire." / "Reconnexion du suivi…", and the Sign out button now works even if the session is already gone (#10).
- **Opening a book no longer replays its whole history.** The live progress stream used to resend every past event of the book — thousands for a translated volume — making the workspace reload itself every two seconds for many minutes (up to about half an hour on the largest production book) and loading the server for nothing. A newly opened book now only receives what happens from that moment on; reconnections still resume exactly where they stopped (#12).
- **"Configure selection" no longer rewrites settings you did not touch.** Applying a common provider (or any other batch setting) to several books silently reset their target language to French and their quality to "High quality". Both fields now default to "keep each book's own value", like the provider, memory source and instructions fields (#13).
- **Database growth.** Every model request was stored with its prompt three times over: once as the prompt, once more inside the saved request parameters, and again in the context trace — which also kept the full text of every context item that was *not* used. On a busy instance the `llm_requests` table grew by about 1.5 GB per day and filled the production disk, interrupting translations. New requests now store the prompt once; the trace still explains which context items were kept or dropped and why (source, relevance, size, reason, short excerpt for dropped items). The cache, the statistics and the request inspector are unaffected. Existing rows can be compacted with `docker compose exec api python -m app.maintenance.compact_request_logs` (add `--dry-run` to measure first; see the module help for reclaiming disk space with `VACUUM FULL`) (#11).
- **Resuming on another provider.** When a job runs on a recovery provider different from the book's own, prompts are now sized for that provider's context window. They used to be sized for the book's configured provider — overflowing a smaller fallback model — and a job that brought its own provider to a book without one could never start (#14).
- **Clearer API errors.** Deleting a provider that is still in use answered "Cette entrée existe déjà, ou une référence est invalide"; it now says exactly what still refers to it (how many books, jobs or history requests) and what to do. When an external service — OpenViking memory, the Codex connector, SearXNG or a provider being tested — is down or unreachable, the affected action answers "service externe injoignable" (HTTP 502) where it used to show a generic server error with a diagnostic reference (#16).
- **CI.** The PostgreSQL migration job left a ~200 MB orphan database volume on the runner after every pipeline, which eventually filled the shared runner's disk and made every job of every pipeline fail. The throwaway database now lives in memory and its container is removed together with its volumes (#20).
- **Archived books stay at rest.** Resuming or retrying an old job of an archived book, or choosing its first provider, could restart paid model calls on a book that was supposed to be shelved. Both are now refused or skipped until the book is restored. A failed job can no longer be "paused" — which used to turn it back into a blocking job and prevent any new work on the book — and starting a second job on a busy book answers with a proper conflict (HTTP 409) and no longer looks like invalid input (#15).
- **EPUB import robustness.** A failed import (validator failure, database error, or a project archive that does not match its EPUB) left the uploaded file behind on disk, one more copy per attempt; it is now removed with the rolled-back import. Books whose title, author or language metadata exceed the database limits are imported with truncated metadata and no longer end in a server error, and an EPUBCheck run that does not finish within 90 seconds no longer refuses the book: the informative source report is simply marked unavailable (at export time the same timeout now gives an explicit message) (#17).
- **Interface details.** The Strategy tab shows the book's word count again (French showed none, English replaced the word "words" with the number); "Récupérer 1 passage(s)" is now properly singular or plural; raw keys such as `status.none`, `critique_acceptance` or `already_analyzed` no longer appear in status lines; the book deletion prompt and button follow the interface language and the refresh time follows its locale; the same EPUB, project archive or glossary file can be selected again after a failed import without reloading the page; and the stage indicator returns to the live pipeline stage a few seconds after an export, where it used to stay stuck on "Export" (#18).
- Resolving the last quality alert of a passage now clears its "À vérifier" flag, as accepting or rejecting a suggestion already did; the book no longer keeps reporting a passage to review when nothing is left to handle (#19).

### Changed

- Unexpected API errors (HTTP 500) and failed jobs now log *where* they happened (`trace=` with file, line and function) next to the diagnostic reference shown in the interface. Exception messages are deliberately left out so that service logs still never contain book text (#6).

## [0.3.5] - 2026-09-17

### Fixed

- The retry delay configured in *Settings → Automatic recovery* is now the one actually used when a provider is unavailable; the first retry waits exactly that delay and the exponential backoff starts from the second retry.

## [0.3.4] - 2026-09-17

### Added

- Native Anthropic Claude API provider (`kind: anthropic`) supporting Claude 3.5 Sonnet, Haiku, Opus and earlier models with direct Messages API integration.
- Native OpenAI ChatGPT API provider (`kind: openai_direct`) supporting GPT-4 and GPT-3.5 models with direct Chat Completions API integration.

### Changed

- Exponential backoff with jitter for provider retries (60s → 120s → 240s → 480s → 960s → 1920s → 3600s cap) to prevent rapid retry loops on persistent failures.
- Provider `Retry-After` headers now take priority over configured delays.
- Added configurable retry parameters: `PROVIDER_RECOVERY_BASE_SECONDS` (default 60), `PROVIDER_RECOVERY_MAX_SECONDS` (default 3600).
- Added `SESSION_DURATION_HOURS` configuration (default 24).
- Structured error codes with retry eligibility and severity metadata.

### Fixed

- Persistent timeout loops when Luna provider returns HTTP 504 at 600s limit (observed 254 retry attempts on Vol. 18).

## [0.3.3] - 2026-09-16

### Fixed

- Normalize hybrid EPUB 2 exports containing EPUB 3/HTML5 markup, invalid or duplicate XML IDs, and EPUB 3-only spine attributes so EPUBCheck accepts translated books while internal links remain valid.

## [0.3.2] - 2026-09-16

### Fixed

- Kept the current application available while the pre-deployment PostgreSQL dump runs, reducing the service interruption to the migration and image switch.
- Made deployment retries for an already healthy commit idempotent and hardened migration cancellation and rollback checks.
- Added and published a self-contained Docker Hub overview with beginner installation, verification, update and backup instructions.
- Removed the blocking glossary-addition prompt from manual validation. A dedicated glossary panel now adds and locks terms explicitly.

## [0.3.1] - 2026-09-14

### Changed

- Aligned both interface themes with the Libris logo using midnight indigo, violet, pale lavender and coral tokens while preserving accessible contrast.
- Added a beginner-oriented Docker Hub deployment path and public Docker operations guide.
- Made GitLab the canonical release pipeline: tested commit-addressed images are promoted to GitLab Container Registry and Docker Hub, while the GitHub push mirror creates the matching GHCR release.
- Added a serialized, forced-command deployment from protected GitLab tags to the existing CT116 production stack, including a pre-deployment PostgreSQL backup and health verification.
- Allowed the destructive worker-recovery smoke test to target an explicitly named disposable Compose project.

## [0.3.0] - 2026-09-14

### Added

- Galley Proof visual system with tokenized typography, spacing, color, light/dark themes and reduced-motion behavior.
- Responsive global navigation and native workspace/chapter selectors for phone layouts.
- Keyboard focus trapping, Escape dismissal and trigger-focus restoration for inspectors and previews.
- Skip navigation, current-page semantics and 44 px phone touch targets.

### Changed

- Consolidated the interface into one canonical stylesheet and replaced mobile library tables with readable records.
- Refreshed public screenshots from an isolated API-backed installation containing only fictional EPUB fixtures.
- Browser integration tests require a loopback `LIBRIS_E2E_URL`, explicit credentials and `LIBRIS_E2E_CONFIRM_DISPOSABLE=1`; workspace tests accept `LIBRIS_E2E_PROJECT_ID` or `LIBRIS_E2E_STATE` for their prepared fixture.

### Fixed

- Removed horizontal document overflow at tablet widths and restored access to every workspace section on narrow screens.
- Made the multi-book browser test follow the required archive-before-delete workflow.

## [0.2.6] - 2026-09-14

### Added

- Safe marker restoration for targeted revisions when unchanged text boundaries provide an unambiguous alignment.
- Adaptive reasoning fallback to `none` when reasoning consumes the response without producing usable final content.

### Fixed

- Marker and truncation failures now enter the bounded small-batch repair path without five identical retries.
- Early failures report the actual number of attempts instead of a misleading `2/5` suffix.
- Validation failures retain their actionable internal reason instead of a generic `ValueError` label.

## [0.2.5] - 2026-09-14

### Fixed

- Targeted revisions that alter immutable EPUB markers now receive one explicit marker-repair retry instead of five identical retries.
- Persistent marker violations retain the current translation and report an actionable reason in the quality issue.

## [0.2.4] - 2026-09-14

### Added

- Bulk ZIP download of complete translated EPUBs selected in the library.

### Fixed

- Changing a project provider while work is paused now updates both the suspended job and any explicit recovery-provider override.

## [0.2.3] - 2026-09-14

### Added

- First-class per-provider reasoning control: unsupported, model default, disabled, minimal, low, medium, high or extra high.
- Specific diagnostics when a provider returns reasoning but no final content.

### Fixed

- Reasoning settings now respect the declared capability consistently across Chat Completions, Responses and Codex transports.

## [0.2.2] - 2026-09-14

### Added

- Signed-in users can change their username while preserving active sessions.

### Fixed

- Username changes reject conflicts with an existing account.
- The login username starts empty and browser autofill is disabled on the login form.

## [0.2.1] - 2026-09-14

### Security

- Patch four bundled EPUBCheck dependency JARs with SHA-256-pinned Jackson 2.18.8 and HttpCore 5.4.3 artifacts. Upstream EPUBCheck 5.3.0 remains the latest release; launcher filenames are preserved, updated versions are recorded in JAR metadata.
- Remove pip and its vendored build tooling from the runtime image after installation.
- These changes address seven fixable HIGH findings discovered by the full container scan after the application dependency checks.

## [0.2.0] - 2026-09-14

### Added

- Account page with password changes and individually revocable sessions.
- Administrator controls for roles, account deactivation/reactivation and password resets.
- Configurable provider recovery delay (5–3600 seconds, default 60), preserving checkpoints and provider capacity limits.
- Automatic analysis-to-translation pipeline, one targeted recovery pass and full AI review.
- Library sorting by status/model, visible status sort control and batch memory-source selection.
- Desktop/mobile account and recovery browser tests, and the full test suite on PostgreSQL in CI.

### Fixed

- Chained jobs display their real translation/review stage instead of their initial analysis operation.
- Temporary provider failures preserve accepted corrections for retry.
- Obsolete correction-failure issues are closed when a successful correction clears the remaining critiques.
- Duplicate EPUB imports are detected per owner, including archived projects.
- Logout ends only the current session; password resets and account permission changes revoke all affected sessions.

### Security and upgrade

- Unknown-account password checks use a dummy hash; password-change attempts are rate limited.
- Content Security Policy and browser permissions restrictions complement existing origin checks.
- Migration `e92fa613bc10` adds `users.active`, defaulting existing accounts to active. Run migrations before starting the new API/worker.
- Provider retries now use the configured fixed delay rather than exponential backoff. Provider `Retry-After` and concurrency limits still take precedence. Authentication errors, manual pauses and refusals are not automatically retried.
- See [audit and operational notes](docs/audit-0.2.0.md) for coverage and remaining limitations.

## [0.1.0] - 2026-09-13

### Added

- Resumable EPUB analysis, translation, review and recovery jobs.
- Human review workspace, version history, glossary and character memory.
- Series-aware terminology and decisions, reversible project archives and batch controls.
- French and English interface catalogs.
- OpenAI-compatible, Responses API and optional Codex transports.
- Internal memory with optional OpenViking integration.
- EPUB reconstruction and EPUBCheck validation.

### Security

- Non-root application containers, restricted capabilities and private database/bridge ports.
- Encrypted provider credentials, origin checks and hardened session cookies.
- Bounded EPUB archive parsing and synthetic-only public screenshots.

[0.1.0]: https://github.com/HeartBtz/Libris/releases/tag/v0.1.0
[0.2.0]: https://github.com/HeartBtz/Libris/releases/tag/v0.2.0
[0.2.1]: https://github.com/HeartBtz/Libris/releases/tag/v0.2.1
[0.2.2]: https://github.com/HeartBtz/Libris/releases/tag/v0.2.2
[0.2.3]: https://github.com/HeartBtz/Libris/releases/tag/v0.2.3
[0.2.4]: https://github.com/HeartBtz/Libris/releases/tag/v0.2.4
[0.2.5]: https://github.com/HeartBtz/Libris/releases/tag/v0.2.5
[0.2.6]: https://github.com/HeartBtz/Libris/releases/tag/v0.2.6
[0.3.0]: https://github.com/HeartBtz/Libris/releases/tag/v0.3.0
[0.3.1]: https://github.com/HeartBtz/Libris/releases/tag/v0.3.1
[0.3.2]: https://github.com/HeartBtz/Libris/releases/tag/v0.3.2
[0.3.3]: https://github.com/HeartBtz/Libris/releases/tag/v0.3.3
[0.3.4]: https://github.com/HeartBtz/Libris/releases/tag/v0.3.4
[0.3.5]: https://github.com/HeartBtz/Libris/releases/tag/v0.3.5
[0.4.0]: https://github.com/HeartBtz/Libris/releases/tag/v0.4.0
[0.4.1]: https://github.com/HeartBtz/Libris/releases/tag/v0.4.1
[0.5.0]: https://github.com/HeartBtz/Libris/releases/tag/v0.5.0
