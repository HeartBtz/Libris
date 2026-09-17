# Changelog

All notable changes are documented here. Libris follows [Semantic Versioning](https://semver.org/); while the project is below 1.0, minor versions may include breaking operational changes that are called out explicitly.

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
