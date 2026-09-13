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
