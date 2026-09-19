# Security policy

## Supported versions

| Version | Security fixes |
| --- | --- |
| Latest `0.x` release | Yes |
| Older releases and commits | No |

## Reporting a vulnerability

Please report vulnerabilities privately, never in a public issue.

- On GitHub, use **Security → Report a vulnerability** when private reporting is enabled.
- Otherwise, contact the repository owner privately through the hosting platform and wait for a private channel
  before sharing details.

Include the affected version or commit, how Libris is deployed, what you expected and what happened, and a
minimal reproduction with synthetic data. Remove secrets, cookies and book content from logs and screenshots.

Libris is maintained by a small team without a guaranteed response time. You will get an acknowledgement when
the report is reviewed, and fixes are released as soon as practical.

## Running Libris safely

- Keep PostgreSQL and the optional Codex bridge on the private Compose network; only the web application should
  publish a port.
- Use HTTPS for any access beyond the local machine, and set `COOKIE_SECURE=true` behind it.
- Protect the `.env` file and back it up with the data: losing `SECRET_KEY` makes saved provider credentials
  unreadable.
- Translation sends the passages being processed to the providers you configure. Choose them according to your
  privacy requirements.

The security design (authentication, sessions, tokens, isolation between users, container hardening) is
described in [docs/architecture.md](docs/architecture.md#security-design); installation hardening is in
[docs/docker.md](docs/docker.md) and [docs/configuration.md](docs/configuration.md).
