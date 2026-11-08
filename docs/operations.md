# Operations

## Weekly schedule

The GitHub Actions workflow runs at 19:30 UTC every Sunday, which is Monday 03:30 in Asia/Shanghai. GitHub scheduled workflows can start later than the requested minute during periods of platform load.

The production job is guarded by repository variable `OVERSEAS_STORIES_PRODUCTION_ENABLED`, which defaults to false. Before cutover, scheduled executions are intentionally skipped. Do not set the variable to true until the new GitHub Runner passes real, automatic M1–M4 integration validation and the legacy server's timer is disabled.

Once enabled, the workflow executes, in order:

1. M1/M2: `python -m overseas_stories.cli sync --source all --ai-limit 5`
2. M3: `python -m overseas_stories.editorial --apply`
3. M4: `python -m overseas_stories.localize --apply --limit 5`

M2 and M4 each have independent limits of five model calls per run. A failure stops later stages; rerunning is safe because source and derived records are idempotent and source hashes prevent unnecessary model calls.

## Automated preflight

Set `OVERSEAS_STORIES_INTEGRATION_ENABLED=true` only after the required repository secrets are present. The independent `integration-preflight.yml` runs on relevant main pushes and a weekly schedule; it verifies a read-only PostgreSQL query and indie.md dry-run without incurring LLM fees. A passing result does not replace a successful real M1–M4 E2E or permission to cut over.

## Manual run

Open **Actions → Weekly overseas founder stories → Run workflow** in GitHub. Manual and scheduled runs share a concurrency group, so they will not overlap. This is an emergency operations mechanism, not an acceptance-test requirement; automated checks run on push/PR.

## Required configuration

The production schedule is gated by the Actions variable `OVERSEAS_STORIES_PRODUCTION_ENABLED`, which defaults to false. The read-only preflight schedule is gated by `OVERSEAS_STORIES_INTEGRATION_ENABLED`, also false by default. A manual preflight run is allowed even while the scheduled preflight is disabled.

Actions secrets:

- `DATABASE_URL`
- `AI_EXTRACTION_ENDPOINT`
- `AI_EXTRACTION_API_KEY`
- `AI_EXTRACTION_MODEL`
- `SUPABASE_CA_CERT` (optional PEM CA certificate; required if the URL uses `sslmode=verify-full`)

Actions variables:

- `OVERSEAS_STORIES_INTEGRATION_ENABLED` (defaults to `false`; enables scheduled read-only preflight)
- `OVERSEAS_STORIES_PRODUCTION_ENABLED` (defaults to `false`; enables scheduled production M1–M4)
- `SUBSTACK_CONTENT_AUTHORIZED` (defaults to `false`; enables Substack only when authorized)

A manual run of **Integration preflight (read-only)** performs one authenticated model request using synthetic input and no database writes.
- `OVERSEAS_STORIES_PRODUCTION_ENABLED` (defaults to `false`; cutover switch)
- `OVERSEAS_STORIES_INTEGRATION_ENABLED` (defaults to `false`; automated read-only integration gate)

## Troubleshooting

- **Missing secret**: add the named secret in repository Settings → Secrets and variables → Actions.
- **Database connection failure**: check the pooler/direct connection URL, username, password, TLS mode, and the runner role's grants.
- **Gateway timeout**: verify that the HTTPS gateway is reachable from public GitHub-hosted runners and that the configured model is available. Do not switch to a private Docker hostname; the GitHub runner is outside that network.
- **Substack skipped**: this is intentional until the authorization variable is explicitly set to `true`.
- **M3/M4 failure**: inspect the failing step's output. Do not reset the database or re-run schema migrations as a troubleshooting shortcut.
