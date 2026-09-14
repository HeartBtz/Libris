# Changelog

All notable changes are documented here. Libris follows [Semantic Versioning](https://semver.org/); while the project is below 1.0, minor versions may include breaking operational changes that are called out explicitly.

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
