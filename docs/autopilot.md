# Autopilot

This page is for anyone who launches translations, in the interface or through the
[automation API](api.md), and for administrators who tune how Libris behaves when something goes
wrong. It explains what happens between "Translate" and the finished book, how Libris recovers from
failures, what "kept in the original" means, where every automatic decision is logged, and how to turn
the autopilot off.

With the autopilot, a book goes from its source to a finished output with no human step. Every point
where a person used to decide (a refused passage, an invalid model answer, a review remark, a proposed
glossary term, a provider outage) is decided by Libris or by the model, within fixed limits, and
logged with its reason. You can still correct any passage at any time; you never have to.

## When it applies

The autopilot is **on by default**. It applies to every job that covers a whole book:

- an import started with its pipeline, in the import assistant;
- an automation API request;
- the **Analyze** and **Translate** actions on a whole book.

Targeted work stays under your control and keeps its normal behaviour: translating one chapter or
one passage, retrying a selection from **Summary & recovery**, or applying accepted proposals.

Whether a book uses it is decided in this order:

1. the launch itself (`"autopilot": false` in `POST /api/projects/{id}/jobs`);
2. the book's own choice: **Settings** tab of the book › **Autopilot** (**On**, **Off: decisions wait
   for a person**, or **Installation setting**);
3. the installation's choice: **Settings › Autopilot › Run books on autopilot**, or the
   `AUTOPILOT_ENABLED` environment variable (see [configuration](configuration.md)).

## The stages of a book

1. **Import.** The source (EPUB, text chapters, Markdown, HTML, DOCX, or a JSON request) is cut into
   passages, the unit of every model call. See [architecture](architecture.md#from-source-to-passages).
2. **Analysis.** Libris reads each passage to build chapter summaries, characters, relations and a
   narrative state, then consolidates them into the **Book Bible**. Under the autopilot, a passage
   whose analysis is refused or keeps coming back invalid is skipped, and so is a failed Book Bible
   batch; both are logged. Then the proposed glossary terms, series identity links and the Book Bible
   are decided (see [Memory decisions](#memory-decisions)).
3. **Translation.** Each passage is translated, then improved according to the book's quality level
   (see below). Several passages of a book are translated at once, within the provider's capacity.
   Under the autopilot, a review, revision, polish or context plan that fails is skipped and the
   existing translation is kept; a failed translation marks the passage as failed without stopping
   the book.
4. **Convergence rounds.** At most `AUTOPILOT_MAX_ROUNDS` rounds (3 by default), each made of:
   1. the [recovery ladder](#the-recovery-ladder) for every failed or untranslated passage;
   2. on the first round, at high or maximum quality, a **global consistency** check across the book;
   3. the [final review](#the-final-review): the whole book on the first round (only the new chapters
      when the job [follows up a volume](#following-up-a-volume)), then only the passages still open
      and those recovered during the round;
   4. [AI arbitration](#ai-arbitration) of everything still open.

   The loop stops as soon as no passage is failed or open.
5. **Settling.** Whatever is left after the last round is closed: open points are closed on the
   current translation, and a passage that is still failed [keeps its original text](#kept-in-the-original).
   No machine passage ends as "to check", error, refused or untranslated.
6. **Export.** The book can be exported (EPUB, text, Markdown…) from the interface, or the API
   request builds its result and report (see [the API guide](api.md#get-the-result)).

Passages a person corrected or validated are never changed by any of these steps, even while they
are still marked "to check". The round and phase are saved as the job goes: a paused or interrupted
job resumes where it stopped, without redoing what is settled.

### What each quality level does

| Quality | Per passage | Whole book |
| --- | --- | --- |
| `fast` | Translation | Final review |
| `normal` | Translation, then a review that can flag the passage | Final review |
| `high` | Translation, review, and a revision when the review found something | Global consistency, final review |
| `maximum` | As `high`, plus a polishing pass | Global consistency, final review |

At high and maximum quality, the book's **Review mode** can be **Review and revision in one call**
(`REVIEW_MODE=fused` for the installation): one model call returns the problems and, if needed, the
corrected text, instead of two calls. It applies only to the first review of a machine translation;
if the call cannot fit the model's window or keeps failing, Libris falls back to the two separate
calls and logs why.

## The recovery ladder

A passage the translation gave up on climbs these steps, cheapest first, until one works:

1. **Informed retry.** The same provider is asked again and told why the previous answer failed.
2. **Batch repair.** A passage of several paragraphs is translated in groups of four paragraphs,
   then reassembled.
3. **Sentence split.** Each paragraph is cut at sentence boundaries, translated piece by piece, then
   reassembled.
4. **Reduced context.** Only the passage and its mandatory rules (instructions, locked glossary), with
   no neighbours or memory.
5. **Fallback providers.** On each [fallback provider](#fallback-providers-and-outages): an informed
   retry, then the sentence split.
6. **Last resort.** The passage [keeps its original text](#kept-in-the-original), with the list of
   attempts as the reason.

Every answer goes through the usual checks (same paragraphs, markers intact, locked glossary
respected). Each step is bounded (one call, or one per group), and every outcome is logged.

### Kept in the original

"Kept in the original" (`source_retained`) means Libris could not obtain a valid translation for a
passage, so the output contains the passage's source text instead. It is the only way a passage can
remain untranslated under the autopilot, and it is always visible:

- the passage carries a warning with the reason, and appears in the book's **Autopilot** tab under
  **Passages kept in the original**;
- the job's report counts it as a residual, and the API result lists it with its reason (a request with
  residuals ends `completed_with_residuals`);
- exports write the source text for it.

If the passage already had a machine translation and only a later attempt failed, Libris keeps that
earlier translation instead of reverting to the source. A kept passage is not a human correction: the
automatic passes leave it alone, but you can have it translated again at any time, by retranslating the
passage in the editor or by selecting it in **Summary & recovery** (with another provider if you
like). A successful translation replaces the original and settles its warning; if the attempt fails
again, the passage stays in the original with the new error. You can also translate it yourself; your
version then replaces it like any human correction.

## Fallback providers and outages

When a provider stops answering, Libris moves along a chain of providers, in this order:

1. the provider the job is using;
2. the book's provider;
3. the book's own fallback providers (book **Settings** › **Autopilot** › **Fallback providers**);
4. the installation's fallback providers (**Settings › Autopilot**, or `AUTOPILOT_FALLBACK_PROVIDERS`,
   names or ids separated by commas).

An outage is first waited out as usual, with growing delays (see **Settings › Automatic recovery** in
[configuration](configuration.md)), at most `AUTOPILOT_OUTAGE_MAX_RETRIES` times (5) and
`AUTOPILOT_OUTAGE_MAX_WAIT_SECONDS` in total (3600). After that, or at once when the provider refuses
its credentials, the job switches to the next provider in the chain and logs it. The book's own
provider setting is not changed.

When no provider in the chain answers, the job ends **failed** with the reason
(`stop_reason = providers_exhausted`): it never waits forever.

## AI arbitration

Open points are what a person used to arbitrate: the reviewers' remarks, the model's doubts
(uncertainties), global consistency remarks, warnings from the automatic checks, and any other
unresolved issue on the passage.

- One model call per passage decides all of its points at once, and only passages with open points are
  sent. The model returns a decision for each point and only the paragraphs it changes.
- A correction is applied only if the resulting text passes the same checks as a translation.
  Otherwise all the points of that call are rejected with that reason, and the translation stays as
  it is.
- A call that fails (a refusal, an error) leaves the points open for the next round.
- After arbitration, the automatic checks run again on the new text; a warning already decided is not
  raised a second time.

## The final review

The final review looks again at translated passages with the book's full context, after the
consistency check:

1. It reassesses the **current** text (an earlier remark may already have been fixed).
2. If a problem or doubt remains, it asks for one complete corrected version, keeping identifiers,
   markers and locked terms.
3. It checks that correction with a second model call and the automatic checks.
4. It applies the correction only if nothing is flagged any more; otherwise the text is left as it was.

Each passage gets at most one correction cycle per review. Passages corrected or validated by a
person, and passages kept in the original, are never reviewed. A refusal or invalid answer on one
passage leaves it open and does not stop the review of the book. A resolved passage leaves the queue
without being marked "validated by a person".

Under the autopilot the final review is part of each round. Without it, a whole-book translation runs
it once at the end, and **Review log › Start AI review** runs it on demand on the passages still to
check. `FINAL_REVIEW_ENABLED=false` turns off the automatic review (the button still works), and an
API request can skip it with `final_review: false`.

### Following up a volume

When new chapters are added to a volume that is already translated (a webnovel sent over time, by an
import into an existing volume or by the [automation API](api.md#following-a-series-over-time)), the
job covers only the new or replaced chapters and any chapter still missing a translation: the global
consistency check samples only their occurrences, and the final review reads only their passages.
Chapters already delivered are neither translated nor reviewed again; they give their context to the
new ones.

### Optional web search (SearXNG)

When an administrator configures a SearXNG instance (**Settings › SearXNG**, see
[configuration](configuration.md)), the final review can check a term or reference on the web when the
model asks for it:

- at most two searches per passage, each of at most 200 characters, three results each, 15 seconds
  per call;
- the results (URL, title, snippet) are treated as untrusted hints, never as instructions, and no
  result page is downloaded;
- the sources and the explanation are kept in the book's events, and the context sent to the model is
  visible in the request traces;
- a search that fails is not a confirmation.

The searched terms are sent to SearXNG and possibly to its upstream engines: enable it only if that is
acceptable for your books, and only towards an instance you control. The SearXNG instance must allow
the JSON format (`search.formats` must include `json` in its `settings.yml`). Without SearXNG, the
review relies on the book, its memory and the glossary.

## Memory decisions

Without any model call, from evidence already in the database:

| Proposal | Decision | Threshold (setting) |
| --- | --- | --- |
| Proposed glossary term | Confidence from its occurrences in the source text (none: 0; one: 0.6; two: 0.8; three or more: 1). Accepted at or above the threshold, otherwise removed. | `AUTOPILOT_GLOSSARY_MIN_CONFIDENCE` (0.75) |
| Ambiguous series identity link | Confidence = shared names / all names, adjusted by gender. The best candidate is linked if it reaches the threshold with a lead of at least 0.1; otherwise all candidates are rejected and the character stays specific to the volume. Two identities are never merged. | `AUTOPILOT_IDENTITY_MIN_CONFIDENCE` (0.8) |
| Book Bible | Validated when the share of analysed passages reaches the threshold. | `AUTOPILOT_BIBLE_MIN_COVERAGE` (0.8) |
| Chapter whose context is outdated (an earlier chapter's source changed) | The flag is cleared when the job translated or reviewed enough of its passages again. | `AUTOPILOT_STALE_MIN_COVERAGE` (0.5) |

Decisions a person already took are never revisited. A link rejected by the autopilot is not proposed
again when the series is refreshed.

## The decision log and the report

Every decision is logged with its stage, kind, action, reason, the provider and model involved, and
the job and passage concerned. In the interface, the book's **Autopilot** tab shows:

- the current round and phase while a job runs, and any fallback provider in use;
- the **Final report** of the last run: outcome, **Convergence rounds**, **Passages kept in the
  original** (each with its reason and links to the passage and its decisions), and the number of
  logged decisions;
- the **Decision log**, most recent first, filterable by stage and by passage.

The same data is available from `GET /api/projects/{id}/autopilot?limit=50&offset=0` (optional
`job_id`, `segment_id`, `stage` filters; `limit` up to 500). It returns whether the autopilot is on
for the book, the limits in force, the last report (with its `job_id`, `status` and `finished_at`) and
the decisions, newest first.

The report stored on the job looks like this:

```json
{
  "outcome": "completed_with_residuals",
  "rounds": 2,
  "residuals": [{"segment_id": "…", "chapter_id": "…", "reason": "Texte original conservé automatiquement : …"}],
  "reason": "1 passage(s) conservé(s) dans la langue d’origine faute de traduction valide.",
  "quality": {"scored": 411, "average": 91.4, "minimum": 40, "to_review": 6, "review_below": 70,
              "bands": {"good": 380, "fair": 25, "weak": 5, "poor": 1}, "histogram": [0, 0, 0, 0, 1, 2, 3, 10, 35, 360]}
}
```

`outcome` is `completed`, `completed_with_residuals` or `failed`. `quality` sums up the passage
[quality scores](architecture.md#passage-quality-scores) once the run is settled: the recovery steps,
the arbitrations and the points closed without a correction all lower a passage's score, so the
book's **Quality** tab lists first the passages the autopilot had the most trouble with.

### Cost

The autopilot only spends calls where something is wrong. The recovery ladder only concerns failed
passages; arbitration makes one call per passage that has open points; later rounds only review the
passages still open or just recovered. The memory decisions make no call at all.

## Settings

Administrators set the installation's behaviour in **Settings › Autopilot**. Values saved there apply
to the next decision without a restart and win over the environment variables until you choose **Go
back to the environment values** (see
[settings changed in the interface](configuration.md#settings-changed-in-the-interface)).

| Setting | Environment variable | Default |
| --- | --- | --- |
| Run books on autopilot | `AUTOPILOT_ENABLED` | `true` |
| Convergence rounds at most | `AUTOPILOT_MAX_ROUNDS` | 3 (1–10) |
| Fallback providers | `AUTOPILOT_FALLBACK_PROVIDERS` | none |
| Waits for an outage at most | `AUTOPILOT_OUTAGE_MAX_RETRIES` | 5 |
| Waiting for an outage | `AUTOPILOT_OUTAGE_MAX_WAIT_SECONDS` | 3600 seconds |
| Minimum confidence of a glossary term | `AUTOPILOT_GLOSSARY_MIN_CONFIDENCE` | 0.75 |
| Minimum confidence of a series identity link | `AUTOPILOT_IDENTITY_MIN_CONFIDENCE` | 0.8 |
| Minimum coverage of a Book Bible update | `AUTOPILOT_BIBLE_MIN_COVERAGE` | 0.8 |
| Minimum coverage of an outdated chapter context | `AUTOPILOT_STALE_MIN_COVERAGE` | 0.5 |

Each book can override the on/off switch and add its own fallback providers in its **Settings** tab.
The same book settings are available as `config.autopilot` and `config.fallback_provider_ids` in
`PUT /api/projects/{id}`.

## Without the autopilot

Turn the autopilot off for a book (or for the installation) when you want to take the decisions
yourself. The pipeline then behaves like this:

- **Invalid answers.** An answer rejected by the checks (missing paragraphs, broken markers, locked
  glossary not respected, invalid JSON) is asked again with the reason, up to three times; network
  errors get up to five attempts. A translation or revision that still fails is then repaired in
  groups of four paragraphs (passages of 2 to 128 paragraphs), each group keeping its context,
  identifiers and markers. Groups already repaired are reused after an interruption, and only the
  complete, validated result replaces the passage's translation.
- **Refusals.** A content refusal is retried once; after the second refusal the passage is marked
  refused. A refusal during the analysis blocks the job until you act.
- **Failed passages.** A passage still failing after its attempts keeps its existing text, is flagged
  as an error (or refused) in **Quality**, and the job goes on with the next passage. Ten failed
  passages in a row stop a job launched with **Translate** (a successful passage resets the count,
  and resuming resets it too); a pipeline started from the import assistant does not stop.
- **Automatic recovery.** A pipeline started from the import assistant retries its failed and
  untranslated passages once more after the translation. What is left then waits for you.
- **Summary & recovery.** This tab separates the last job's state from the real coverage of the book:
  translated, missing, retained in original, open, unresolved alerts and protected human choices.
  Filter the passages to recover (errors, refusals, not started, blocked), select up to 200, choose a
  **Recovery provider** and **Retry selection**. The server checks the selection and skips protected
  passages; another job on the book must finish or be cancelled first. The book's own provider does
  not change.
- **Provider outages** are waited out with growing delays for as long as needed, and a provider that
  refuses its credentials blocks the job until you fix it and resume.
- **Open points** wait in the **Review log**. Each AI remark shows its doubt and proposed correction.
  **Accept this proposal** replaces only the paragraph concerned and keeps the EPUB markers; **Reject
  this proposal** keeps the current text. Both create a protected human correction; once the last
  proposal of a passage is decided and nothing else is open, the passage leaves the queue. The queue
  stays stable while jobs run, so drafts are not lost. Final editorial validation remains a separate
  action. When you accept a free-form suggestion that did not keep the internal markers, Libris asks
  the book's provider for a structured correction of that paragraph only, checks it, and keeps the
  current text if the answer is invalid or the passage changed meanwhile.
- **A refused or failed passage** can be resolved in the editor in three ways: type a human
  translation; give a **Human analysis** (a summary useful for continuity) in the inspector; or choose
  **Retain source for export**. Retaining the source does not replace a missing analysis. When the
  Book Bible synthesis itself is refused, validating a human Book Bible with a non-empty summary lets
  the job resume.
- **Exports.** A normal EPUB export requires a complete translation. **Partial EPUB · originals
  retained** puts the source text where the translation is missing, under a distinct file name. The
  **Coverage report** lists passages without analysis, without translation and kept in the original;
  a retained original is never presented as a validated translation.

## When a job stops

| Situation | What happens |
| --- | --- |
| You pause | `paused`; it resumes only when you resume it. |
| You cancel | `cancelled`; saved results are kept, and the book can be launched again. |
| The worker stops cleanly | Calls in flight are interrupted; the job goes back to `pending` at its checkpoint. |
| The worker crashes | Another worker takes the job over once its 60-second lease expires. |
| Network error, timeout, HTTP 429 or 5xx | `waiting`, with a retry scheduled after a growing delay (60 seconds up to one hour by default; a provider's `Retry-After` is honoured up to 24 hours). Under the autopilot, the next fallback provider takes over after the bounded wait. |
| The provider refuses its credentials | `blocked` until you fix the provider and resume. Under the autopilot: the next fallback provider, or `failed` when none is left. |
| No provider answers any more (autopilot) | `failed` with `stop_reason = providers_exhausted` and the reason. |
| Refusal during the analysis | `blocked` (`content_refusal`) until you act. Under the autopilot: the passage is skipped and the decision logged. |
| Refusal during the translation | A second attempt, then the passage is marked `refused` and the book goes on. |
| Invalid JSON or structure | Bounded retries with the reason, repair in groups, then a localized error on the passage. |

The worker checks its lease every two seconds, so a pause or cancellation interrupts all the calls in
flight for the book almost at once. Every write is protected by the passage revision and the lease
holder: a late answer never replaces a human correction or a cancelled state. Passages already
finished are skipped when the job resumes, and interrupted passages restart from their last saved
step.

EPUBCheck still runs at export time: full coverage is not a certificate of a valid EPUB, and a model
review does not guarantee literary fidelity.
