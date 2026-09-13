# Compatibility matrix

The labels below reflect evidence available for Libris 0.1.0. They are not predictions based only on similar software.

| Environment | Status | Evidence and limitations |
| --- | --- | --- |
| Docker Engine on Linux/amd64 | Supported, verified | Production and disposable installations use Compose v2 and amd64 images. |
| Docker Compose v2 | Supported, verified | Required by `docker-compose.yml` and `scripts/check_installation.py`. |
| PostgreSQL 17 | Supported, verified | Production configuration and migration CI target PostgreSQL 17. |
| Python 3.13 / Node.js 22 | Supported for development, verified | Used by Docker and CI. Direct host deployment is not documented. |
| Ubuntu / Debian host | Supported under conditions | Use a vendor-supported Docker Engine and Compose v2. Native host services are not tested. |
| Fedora / RHEL / Rocky / AlmaLinux | Untested | Docker deployment may work, but no validation was run. SELinux policy may require local configuration. |
| Windows / macOS | Untested | Docker Desktop may work; filesystem, networking and backup procedures are unverified. |
| WSL 2 | Untested | Prefer Docker-managed volumes rather than Windows-mounted PostgreSQL storage. |
| Linux/arm64 | Untested | Upstream images publish arm64 variants, but Libris has no multi-architecture validation. |
| Cloud VM | Supported under conditions | Use Linux/amd64 with HTTPS, firewalling and persistent storage configured by the operator. |
| Kubernetes / native Windows services | Not supported | No manifests, lifecycle procedures or tests are provided. |

## EPUB scope

Reflowable EPUB 3 files are the verified target. EPUB 2, fixed-layout, RTL-heavy, DRM-protected and very large books are not claimed as supported. Encrypted ZIP entries are rejected. EPUBCheck validates package conformance, not translation fidelity.

## Browser scope

Chromium desktop and simulated mobile viewports are exercised by Playwright. Firefox, Safari/WebKit and physical mobile browsers remain untested.
