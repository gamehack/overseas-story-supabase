# Operations

## Weekly schedule

The GitHub Actions workflow runs at 19:30 UTC every Sunday, which is Monday 03:30 in Asia/Shanghai. GitHub scheduled workflows can start later than the requested minute during periods of platform load.

Production cutover completed on 2026-11-18. The legacy `qjai-overseas-stories-weekly.timer` is disabled/inactive, and the new GitHub Actions M1–M4 workflow completed a successful production run. Subsequent production executions are schedule-only. A one-time push trigger remains restricted to commits that modify this workflow file and include the exact `[overseas-story-cutover-run]` message marker; ordinary pushes do not launch production. No manual dispatch is required.

Once enabled, the workflow executes, in order:

1. M1/M2: `python -m overseas_stories.cli sync --source all --ai-limit 5`
2. M3: `python -m overseas_stories.editorial --apply`
3. M4: `python -m overseas_stories.localize --apply --limit 5`

M2 and M4 each have independent limits of five model calls per run. A failure stops later stages; rerunning is safe because source and derived records are idempotent and source hashes prevent unnecessary model calls.

## Automated preflight

Set `OVERSEAS_STORIES_INTEGRATION_ENABLED=true` only after the required repository secrets are present. The independent `integration-preflight.yml` runs on relevant main pushes and a weekly schedule; it verifies a read-only PostgreSQL query and indie.md dry-run without incurring LLM fees. A passing result does not replace a successful real M1–M4 E2E or permission to cut over.

## Required configuration

The production schedule is active and runs automatically every Monday at 03:30 Asia/Shanghai. The read-only preflight remains gated by `OVERSEAS_STORIES_INTEGRATION_ENABLED`; it has no manual dispatch and runs only on relevant pushes and its weekly schedule.

Actions secrets:

- `DATABASE_URL`
- `AI_EXTRACTION_ENDPOINT`
- `AI_EXTRACTION_API_KEY`
- `AI_EXTRACTION_MODEL`
- `SUPABASE_CA_CERT` (required PEM CA certificate; used with `sslmode=verify-full`)

Actions variables:

- `OVERSEAS_STORIES_INTEGRATION_ENABLED` (defaults to `false`; enables scheduled read-only preflight)
- `SUBSTACK_CONTENT_AUTHORIZED` (defaults to `false`; enables Substack only when authorized)

The scheduled **Integration preflight (read-only)** performs one authenticated model request using synthetic input and no database writes, in addition to its read-only database and source checks.

## Troubleshooting

- **Missing secret**: add the named secret in repository Settings → Secrets and variables → Actions.
- **Database connection failure**: check the pooler/direct connection URL, username, password, TLS mode, CA certificate secret, and the runner role's grants. Preserve `sslmode=verify-full`; do not embed the old server-local CA file path in `DATABASE_URL`.
- **Gateway timeout**: verify that the HTTPS gateway is reachable from public GitHub-hosted runners and that the configured model is available. Do not switch to a private Docker hostname; the GitHub runner is outside that network.
- **Substack skipped**: this is intentional until the authorization variable is explicitly set to `true`.
- **M3/M4 failure**: inspect the failing step's output. Do not reset the database or re-run schema migrations as a troubleshooting shortcut.
