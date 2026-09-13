# Support

Libris is an early-stage community project without a guaranteed response time.

## Before asking

1. Read the [installation guide](docs/installation.md) and its troubleshooting section.
2. Check `docker compose ps` and `docker compose logs --since=5m api worker`.
3. Reproduce the issue with synthetic or legally shareable data.
4. Search existing issues before opening a new one.

Use the bug-report template for reproducible defects and discussions for usage questions when the hosting platform provides them. Include the Libris version or commit, host platform, Docker/Compose versions, provider transport and relevant sanitized logs.

Never publish `.env`, cookies, provider keys, authentication headers, private books or complete model request bodies. Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).
