# Security audit

This document records the publication review for Libris 0.1.0. It is a bounded engineering review, not a penetration test or certification.

## Verified controls

- Application and Codex containers run as non-root users with dropped Linux capabilities and `no-new-privileges`.
- PostgreSQL and the Codex bridge are not published on host ports by the supplied Compose file.
- Provider credentials are encrypted at rest with the persistent `SECRET_KEY`.
- Session cookies are HTTP-only and same-site strict; secure cookies are configurable for HTTPS.
- EPUB ingestion bounds archive size, entry count, decompression ratio, duplicate paths, symbolic links and unsafe XML handling.
- Preview content is isolated by a restrictive CSP and sandboxed iframe.
- Public screenshots and browser fixtures contain synthetic data.
- Gitleaks scans the full Git history; its only matched tokenizer identifier is explicitly allowlisted as a non-secret.
- `pip-audit` and production `npm audit` reported no known vulnerabilities during 0.1.0 preparation.
- Trivy image scans reported no Critical vulnerabilities during 0.1.0 preparation. Two High Debian findings were remediated by explicitly updating `libpcre2-8-0` during image builds.

## Follow-up of the 2026-09-18 audit (0.5)

- `/openapi.json` requires a session and can be disabled with `OPENAPI_ENABLED=false` (S-4).
- Non-administrators list providers without their address: only identifier, type, name and model. Changing the address or type of a provider that holds a key requires entering the key again (HTTP 409 otherwise), so a stored key is never sent to a new host (S-4).
- The SearXNG client ignores proxy environment variables and does not read answers above 2 MiB, like every other outbound client (S-5). Queries chosen by the model remain an exfiltration channel under prompt injection; enable web search only towards a SearXNG instance you control.
- A shared editor cannot attach a book to a series of the owner that contains books the editor cannot read, which would otherwise pull their terms and decisions into prompts (S-7).
- Authenticated resource exhaustion (S-3): live event streams are bounded per account and per process and poll the database outside the event loop; the declared unpacked size and the whole-archive compression ratio are checked before an EPUB is unpacked; chapter previews reuse a bounded cache; project restores run in the thread pool; EPUBCheck runs are bounded since 0.4.1 (#49).
- Project archives are validated against a versioned schema before anything is written. Restoring never recreates owners, members, permissions or providers: the restoring user becomes the owner, and interrupted jobs come back paused, without provider.
- Glossary TBX files are parsed by the same XML reader as EPUB content: entity declarations are refused, DTDs and the network are never loaded.
- Error messages can be answered in English; the translation only rewrites the `detail` text and never adds internal information.

## Operator responsibilities

- Keep `.env`, PostgreSQL backups, book storage and model traces private.
- Use HTTPS and set `COOKIE_SECURE=true` for remote access.
- Restrict the published API port with a firewall or reverse proxy.
- Review provider privacy terms: selected book content is sent to configured model providers.
- Keep external OpenViking and SearXNG services private and maintained separately.
- Establish retention for model request logs, which may contain book excerpts and responses.

## Known limitations

- Login throttling is process-local and is not a complete internet-facing abuse-control system.
- Dependency and image scans are point-in-time checks; new advisories can appear after release.
- Trivy reports five High findings in libraries bundled by EPUBCheck 5.3.0. This is the latest production-ready upstream release; replacing its JARs independently has not been attempted because that combination is unsupported and untested.
- Trivy also reports two High Python findings from `msgpack` and `setuptools` metadata inherited from the official Python base image. Neither package is installed in the final images according to `pip`, and `pip check` reports a consistent environment.
- Base images use release-line tags rather than immutable digests so security rebuilds can receive upstream fixes.
- Complete backup restoration, arm64, non-Chromium browsers and non-Linux hosts require further validation.
- Rights to the project logo must be confirmed by the distributor before public redistribution.
