# Production cutover and legacy decommission

Do not decommission the old VPS workload until the GitHub Actions workflow has completed one successful end-to-end production run.

## Go/no-go checks

- [ ] GitHub Actions secrets are configured, including the session-pooler `DATABASE_URL`.
- [ ] Manual **Integration preflight (read-only)** passes: Supabase read-only query, one authenticated synthetic model request, and indie.md dry-run.
- [ ] The production workflow is ready, but `OVERSEAS_STORIES_PRODUCTION_ENABLED` remains false until the old timer is stopped.
- [ ] The Substack authorization variable is enabled only if the selected content is authorized for the intended storage and AI processing.

## Disable the old VPS schedule

After the read-only preflight passes, stop the old timer before enabling the new production schedule. Run on the old server:

```bash
sudo systemctl disable --now qjai-overseas-stories-weekly.timer
sudo systemctl stop qjai-overseas-stories-weekly.service || true
systemctl is-active qjai-overseas-stories-weekly.timer || true
systemctl is-enabled qjai-overseas-stories-weekly.timer || true
```

The one-shot service is no longer scheduled. Leave the old container available for rollback until the new GitHub-hosted M1–M4 run succeeds; stopping the timer does not affect Supabase data.

## Enable the new production schedule

After the old timer is confirmed inactive, set the GitHub Actions variable `OVERSEAS_STORIES_PRODUCTION_ENABLED=true`. The next scheduled run is Monday 03:30 Asia/Shanghai. The manual workflow trigger is also available after this gate is enabled.

Verify the first M1–M4 run succeeds and compare database counts. The expected existing data must remain intact; do not rerun SQL migrations.

## Remove the old application container

Only after the first new production run succeeds, remove the old application container. The project directory is expected to be `/opt/qjai-overseas-stories`. Confirm the path before running:

```bash
cd /opt/qjai-overseas-stories
docker compose down
```

Do not use `docker compose down -v`; do not remove shared Docker networks or unrelated containers. The database is remote and must remain untouched.

Verify that the old collector container is absent and the timer is inactive. Other server services, including the shared model gateway, are not part of this decommission.

## Archive the old repository

After server decommission, archive `gamehack/qjai-overseas-stories` as a private historical repository. Do not delete it and do not change its visibility to Public. The new public repository is the only source of truth for code changes.

## Rollback

If the GitHub Actions runtime fails after cutover:

1. Set `OVERSEAS_STORIES_PRODUCTION_ENABLED=false` and confirm no GitHub-hosted production run is active.
2. Restart the old application container if it was removed.
3. Re-enable the old systemd timer only if a temporary rollback is required.
4. Do not run both schedulers concurrently; both target the same Supabase records and could duplicate model spend even though database upserts are idempotent.
