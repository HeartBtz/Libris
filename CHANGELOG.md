# Changelog

All notable changes are documented here. Libris follows [Semantic Versioning](https://semver.org/); while the project is below 1.0, minor versions may include breaking operational changes that are called out explicitly.

## [Unreleased]

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
