# Getting help

Libris is a community project without a guaranteed response time. These steps usually find the answer fastest.

## Before asking

1. Look for your problem in the troubleshooting sections of [docs/docker.md](docs/docker.md) and
   [docs/operations.md](docs/operations.md).
2. Check the state of the stack and its recent logs:

   ```bash
   docker compose ps
   docker compose logs --since=5m api worker
   ```

3. Try to reproduce the problem with synthetic or freely shareable text.
4. Search the existing issues.

## Asking

Open an issue with the bug-report template for a reproducible defect, or use discussions for usage questions
when the platform offers them. Include:

- the Libris version (shown by `curl http://127.0.0.1:8088/health`) or commit;
- the host system and the Docker and Docker Compose versions;
- the kind of provider (OpenAI-compatible server, OpenAI, Anthropic, Codex) and model;
- the relevant log lines, cleaned of secrets.

Never publish your `.env`, cookies, provider keys, authorization headers, books or full model requests.
Vulnerabilities are reported privately, as described in [SECURITY.md](SECURITY.md).
