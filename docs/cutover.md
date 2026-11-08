# Production cutover and legacy decommission

Do not decommission the legacy workload until the GitHub Actions workflow has completed one successful end-to-end production run.

## Go/no-go checks

- [ ] GitHub Actions secrets are configured, including the Supabase session-pooler `DATABASE_URL`.
- [ ] Scheduled **Integration preflight (read-only)** passes: Supabase read-only query, one authenticated synthetic model request, and source-adapter dry-run.
- [ ] The production workflow is ready, but `OVERSEAS_STORIES_PRODUCTION_ENABLED` remains false until the legacy timer is stopped.
- [ ] The Substack authorization variable is enabled only if the selected content is authorized for the intended storage and AI processing.

## Disable the legacy schedule

After the read-only preflight passes, stop the legacy timer before enabling the new production schedule. On the legacy host, substitute the actual timer and service unit names from that host's deployment:

```bash
sudo systemctl disable --now <legacy-timer-name>.timer
sudo systemctl stop <legacy-service-name>.service || true
systemctl is-active <legacy-timer-name>.timer || true
systemctl is-enabled <legacy-timer-name>.timer || true
```

Leave the legacy application container available for rollback until the new GitHub-hosted M1–M4 run succeeds. Stopping the timer does not affect Supabase data.

## Enable the new production schedule

After the legacy timer is confirmed inactive, set the GitHub Actions variable `OVERSEAS_STORIES_PRODUCTION_ENABLED=true`. The next scheduled run is Monday 03:30 Asia/Shanghai.

Verify the first M1–M4 run succeeds and compare database counts. Existing records must remain intact; do not rerun SQL migrations.

## Remove the legacy application container

Only after the first new production run succeeds, remove the legacy application container. From the legacy project's deployment directory:

```bash
docker compose down
```

Do not use `docker compose down -v`; do not remove shared Docker networks or unrelated containers. The database is remote and must remain untouched.

Verify that the legacy collector container is absent and the timer is inactive. Other host services, including any shared model gateway, are not part of this decommission.

## Archive the legacy repository

After server decommission, archive the legacy private repository. Do not delete it and do not change its visibility to Public. This repository is the only public source of truth for future code changes.

## Rollback

If the GitHub Actions runtime fails after cutover:

1. Set `OVERSEAS_STORIES_PRODUCTION_ENABLED=false` and confirm no GitHub-hosted production run is active.
2. Restart the legacy application container if it was removed.
3. Re-enable the legacy systemd timer only if a temporary rollback is required.
4. Do not run both schedulers concurrently; both target the same Supabase records and could duplicate model spend even though database upserts are idempotent.
