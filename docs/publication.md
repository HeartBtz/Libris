# Public release checklist

The source includes AGPL-3.0-only licensing, a beginner Docker guide, GitLab release automation, GitHub mirror automation and screenshots captured from a disposable API-backed installation containing fictional EPUB fixtures. The release procedure is documented in [release.md](release.md).

Before making a GitHub repository public:

1. Review the full Git history for secrets, private hostnames, books and test artifacts — not only the current working tree. Rotate any exposed secret before publication.
2. Confirm ownership/redistribution rights for the supplied logo and included assets.
3. Create the destination repository and push the intended branch only. Do not transfer deployment `.env`, volumes or private backups.
4. Enable GitHub private vulnerability reporting and branch protection. Run the GitHub Actions pipeline on the destination repository.
5. Set a public repository description and screenshot/social preview; do not claim prebuilt images or releases before they exist.
6. For a modified network deployment, make its corresponding source available to users as required by AGPL, with an accessible source link.

## Verified locally

- Fresh Docker Compose installation with a generated configuration, empty PostgreSQL and separate book volumes.
- Successful health endpoint and initial administrator login through a default allowed origin.
- Alembic schema consistency on the fresh PostgreSQL instance.
- API-backed browser screenshots generated only from fictional EPUB fixtures in a disposable Compose project.

GitLab, Docker Hub, the GitHub mirror and both container registries must be checked again for every release. Multi-architecture builds, a complete backup restoration drill and model-quality evaluation remain separate checks until explicitly recorded as successful.
