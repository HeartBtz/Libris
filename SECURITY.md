# Security policy

## Supported versions

| Version | Security updates |
| --- | --- |
| Latest `0.1.x` release | Supported |
| Older commits and releases | Unsupported |

Do not report vulnerabilities through a public issue with exploit details, credentials or private books. If this repository is hosted on GitHub and private vulnerability reporting is enabled, use **Security → Report a vulnerability**. Otherwise contact the repository owner privately through the hosting service before sharing sensitive details.

Include the affected commit, deployment mode, expected and observed behavior, and a minimal reproduction using synthetic data. Remove secrets and user content. You should receive an acknowledgement when the maintainer next reviews private reports; this early-stage project does not guarantee a response or remediation time.

Keep the database and optional Codex bridge private, use HTTPS for remote access, maintain backups, and protect `.env`. Translation requests intentionally send selected book content to the configured providers; choose them according to your privacy requirements.

See the [publication security audit](docs/security-audit.md) for verified controls, operator responsibilities and known limitations.
