# Production cutover and legacy decommission

Do not decommission the old VPS workload until the GitHub Actions workflow has completed one successful end-to-end production run.

## Go/no-go checks

- [ ] GitHub Actions secrets are configured and the weekly workflow can connect to the existing Supabase database.
- [ ] The GitHub-hosted runner can reach the HTTPS model gateway and complete M2/M4 calls.
- [ ] M1/M2, M3, and M4 all complete successfully in one run.
- [ ] Existing database records remain present; compare story, people, facts, project, editorial, and localization counts before and after the run.
- [ ] No duplicate source records were created.
- [ ] The Substack authorization variable is enabled only if the selected content is authorized for the intended storage and AI processing.

## Disable the old VPS schedule

Run on the old server only after all go/no-go checks pass:

```bash
sudo systemctl disable --now qjai-overseas-stories-weekly.timer
sudo systemctl stop qjai-overseas-stories-weekly.service || true
systemctl is-active qjai-overseas-stories-weekly.timer || true
systemctl is-enabled qjai-overseas-stories-weekly.timer || true
```

The service is a one-shot unit; stopping it does not affect the Supabase database.

## Remove the old application container

The project directory is expected to be `/opt/qjai-overseas-stories`. Confirm the path before running:

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

1. Disable the GitHub Actions schedule or disable the weekly workflow.
2. Confirm no GitHub-hosted run is still active.
3. Re-enable the old systemd timer only if a temporary rollback is required.
4. Do not run both schedulers concurrently; both target the same Supabase records and could duplicate model spend even though database upserts are idempotent.
