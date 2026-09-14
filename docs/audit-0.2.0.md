# Libris 0.2.0 — security, functionality and UX review

Date: 2026-09-14. Scope: application code and dependencies, account authorization,
job recovery, migrations, library/account/settings UI. This is an engineering
review with regression tests, not an independent penetration test or a claim
that every possible vulnerability has been excluded.

## Findings and changes

| Finding | Change / evidence |
| --- | --- |
| Provider retry delay was hard-coded and grew to 15 minutes | Settings → Automatic recovery: fixed 60-second default, configurable 5–3600 seconds; provider Retry-After and capacity respected |
| An accepted suggestion could be abandoned during provider outage | Transient failures propagate to persistent job suspension; checkpoint and queued acceptance remain |
| Account management only allowed creation | Admin role changes, reversible deactivation, password resets; own-admin lockout prevented |
| Sessions could not be individually managed | My account lists active sessions and revokes selected sessions; password changes revoke every session |
| Unknown usernames skipped password hashing | Equal-cost dummy-hash verification |
| Password change needed authorization and guessing protection | Current password required, bounded length, origin checks, attempt rate limit |
| Deployment cookie security must match public HTTPS access | Enable COOKIE_SECURE=true for HTTPS installations; direct plain-HTTP browser authentication is then intentionally unavailable |
| Root analyze operation obscured actual progress | Checkpoint translation/review stage takes precedence; resumed status follows checkpoint |
| Failed-job waiting state was opaque | Library shows scheduled retry time or provider-capacity waiting state |
| Mobile account table wrapped names prematurely | Dedicated column sizing, wrapping controls and aligned checkbox labels |

## Account model

- Private instance: administrators create accounts; public registration is not enabled.
- Roles: administrator and user; project memberships retain reader/editor permissions.
- Deactivation preserves projects and history, rejects logins and revokes existing sessions.
- Password changes require the current password. An administrator can reset a forgotten password.
- Passwords use salted PBKDF2-SHA256 (600,000 iterations). Session tokens are random;
  only their SHA-256 hashes are stored. Session list IDs are hashes, not usable login cookies.
- Logout removes the current session only. Password changes/resets and account-role updates
  invalidate all sessions of the affected account.
- No email recovery, MFA, passkeys or native OIDC in this release. Deploy behind the existing
  authenticated reverse proxy when additional identity controls are required.

## Recovery behavior

- Timeout/network/temporary provider errors → waiting → retry from checkpoint after the configured delay.
- Provider-requested Retry-After may extend that delay (bounded to 24 hours).
- A due job may still wait for provider capacity. Retrying does not restart the provider or container.
- Authentication failures, content refusals, manual pauses and cancellations are not blindly retried.
- Already waiting jobs keep their scheduled timestamp; new failures use the latest setting.
- Requesting retry does not guarantee the provider has recovered. Invalid model output remains visible for review.

## Verification

- 133 backend tests passed on SQLite and independently on a disposable PostgreSQL 17 instance.
- Alembic upgrade/check/downgrade/upgrade verified for the account migration on PostgreSQL.
- Three Chromium scenarios passed: library/editor showcase and account/recovery controls at 1440 and 390 px.
- Real-container browser test passed against isolated PostgreSQL: login, recovery configuration,
  account creation/deactivation and session listing, with CSP enabled and no CSP script errors.
- Mobile and desktop screenshots inspected; no horizontal document overflow in tested views.
- Python locked production dependencies: pip-audit found no known vulnerabilities.
- JavaScript production dependencies: npm audit found no vulnerabilities.
- Git history scan: Gitleaks found no secrets before release commit.
- CI now repeats PostgreSQL and desktop/mobile browser checks for branch builds and GitHub releases.

## Remaining operational considerations

- Login/password rate limiting is in-process and IP-scoped, so it resets on API restart and
  is not shared by multiple API replicas. A trusted reverse proxy should enforce edge limits.
- CSP permits inline styles because current React components use dynamic styles; inline scripts
  are not allowed. EPUB preview also has its own restrictive CSP.
- Provider URLs are configured by trusted administrators and may target private services by design.
- PostgreSQL rollback is schema-tested; that is not evidence that a production backup is recoverable.
- Public release automation runs on GitHub. GitLab execution still depends on an available runner.

## Upgrade / rollback

Apply `alembic upgrade head` before replacing API and worker. The new column defaults existing
accounts to active, and does not alter project ownership. Snapshot/backup procedures remain those
of the installation. Rollback to the preceding image can keep the additive column; to reverse the
schema after stopping the new code, downgrade to `d8c7a12f4b9e`. This removes account activation state.

To run PostgreSQL tests locally, set `LIBRIS_TEST_DATABASE_URL` to a **disposable test database**.
The suite drops/recreates application tables; never point it at production.
