# Operations

## Weekly schedule

The GitHub Actions workflow runs at 19:30 UTC every Sunday, which is Monday 03:30 in Asia/Shanghai. GitHub scheduled workflows can start later than the requested minute during periods of platform load.

The production job is guarded by repository variable `OVERSEAS_STORIES_PRODUCTION_ENABLED`, which defaults to false. Before cutover, scheduled executions are intentionally skipped. Do not set the variable to true until the new GitHub Runner passes real, automatic M1–M4 integration validation and the legacy server's timer is disabled.

Once enabled, the workflow executes, in order:

1. M1/M2: `python -m overseas_stories.cli sync --source all --ai-limit 5`
2. M3: `python -m overseas_stories.editorial --apply`
3. M4: `python -m overseas_stories.localize --apply --limit 5`

M2 and M4 each have independent limits of five model calls per run. A failure stops later stages; rerunning is safe because source and derived records are idempotent and source hashes prevent unnecessary model calls.

## Manual run

Open **Actions → Weekly overseas founder stories → Run workflow** in GitHub. Manual and scheduled runs share a concurrency group, so they will not overlap. This is an emergency operations mechanism, not an acceptance-test requirement; automated checks run on push/PR.

## Required configuration

Actions secrets:

- `DATABASE_URL`
- `AI_EXTRACTION_ENDPOINT`
- `AI_EXTRACTION_API_KEY`
- `AI_EXTRACTION_MODEL`
- `SUPABASE_CA_CERT` (optional PEM CA certificate; required if the URL uses `sslmode=verify-full`)

Optional Actions variable:

- `SUBSTACK_CONTENT_AUTHORIZED` (defaults to `false`)
- `OVERSEAS_STORIES_PRODUCTION_ENABLED` (defaults to `false`; cutover switch)

## Troubleshooting

- **Missing secret**: add the named secret in repository Settings → Secrets and variables → Actions.
- **Database connection failure**: check the pooler/direct connection URL, username, password, TLS mode, and the runner role's grants.
- **Gateway timeout**: verify that the HTTPS gateway is reachable from public GitHub-hosted runners and that the configured model is available. Do not switch to a private Docker hostname; the GitHub runner is outside that network.
- **Substack skipped**: this is intentional until the authorization variable is explicitly set to `true`.
- **M3/M4 failure**: inspect the failing step's output. Do not reset the database or re-run schema migrations as a troubleshooting shortcut.
