# Overseas Story Supabase

An open-source, source-aware pipeline for collecting overseas founder stories, extracting evidence-backed facts, building an editorial index, and preparing Chinese-language research records.

The project runs as a scheduled GitHub Actions workload. PostgreSQL/Supabase stores the durable data; no always-on application server is required.

## Pipeline

| Stage | Responsibility |
|---|---|
| M1 — Collection | Read public Markdown journeys from [indie.md](https://indie.md/) and explicitly authorized RSS sources |
| M2 — Extraction | Extract founder identities and structured facts with verbatim source evidence |
| M3 — Editorial index | Build story-scoped project/fact records, profiles, and explainable editorial scores |
| M4 — Chinese research layer | Produce Chinese summaries, outlines, and fact translations while retaining English evidence |

This repository does **not** generate finished articles, publish to WeChat/CMS, or manage downstream distribution. Content production and publishing remain separate systems.

## Design principles

- Source identity is explicit. People or products with the same name are not automatically merged across sources.
- `(source_id, source_item_id)` is the idempotency key for source records.
- SHA-256 hashes detect source changes and avoid unnecessary model calls.
- AI-extracted facts must contain verbatim evidence found in the original source text.
- Founder-reported numbers remain labelled as self-reported; extraction is not independent fact verification.
- Original English source records remain authoritative. Chinese records are a derived reading layer.
- Collection and enrichment are bounded by per-run model-call limits.
- The database schema is private and is not exposed through the Supabase Data API.
- No source content, production database dump, credentials, or publishing credentials belong in this repository.

## Requirements

- Python 3.12+
- A Supabase PostgreSQL database with the project schema applied
- An HTTPS OpenAI-compatible model gateway for M2/M4
- GitHub Actions enabled for scheduled execution

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
cp .env.example .env
```

Set the environment variables shown in `.env.example`. Use a server-side PostgreSQL connection string; never use a browser publishable/anon key as a database credential.

Preview collection without database writes:

```bash
set -a && source .env && set +a
python -m overseas_stories.cli sync --dry-run --source indie-md --ai-limit 0
```

Run the stages separately when operating locally:

```bash
python -m overseas_stories.cli sync --source all --ai-limit 5
python -m overseas_stories.editorial --apply
python -m overseas_stories.localize --apply --limit 5
```

M3 and M4 are safe to rerun: unchanged records are skipped, and model-call budgets are explicit.

## Database setup

The SQL files in `db/` are migration templates for a new installation:

1. Apply `db/schema.sql`.
2. Apply `db/m3_editorial.sql`.
3. Apply `db/m4_localization.sql`.
4. Create a login role for the runner and grant it membership in `overseas_story_runner` (see `docs/database.md`).

Do not reapply initial schema or migrations to an existing production database without reviewing its migration history. This repository does not automatically run DDL against your Supabase project.

The database is expected to use PostgreSQL over TLS. For GitHub-hosted runners, use the Supabase session pooler connection string (port 5432). Prefer `sslmode=verify-full` with the optional `SUPABASE_CA_CERT` secret; at minimum require TLS. Do not retain a server-local CA file path in the connection string.

## GitHub Actions

The scheduled workflow is `.github/workflows/weekly-pipeline.yml`.

- Schedule: every Monday at **03:30 Asia/Shanghai** (Sunday 19:30 UTC).
- Manual trigger: `workflow_dispatch` (operational recovery only; CI and acceptance tests are automatic).
- Order: CI checks → M1/M2 collection → M3 indexing → M4 localization.
- Concurrency: overlapping scheduled runs are serialized.
- The workflow runs on GitHub-hosted runners; it does not require a VPS, Docker daemon, or systemd timer.

Configure these repository **Actions secrets** under **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
|---|---|
| `DATABASE_URL` | Supabase PostgreSQL connection string for a least-privilege runner login |
| `AI_EXTRACTION_ENDPOINT` | HTTPS URL of the OpenAI-compatible model gateway |
| `AI_EXTRACTION_API_KEY` | Model gateway credential |
| `AI_EXTRACTION_MODEL` | Model identifier used for extraction and localization |
| `SUPABASE_CA_CERT` | Optional PEM CA certificate; required when `DATABASE_URL` uses `sslmode=verify-full` |

Optional repository **Actions variable**:

| Variable | Default | Purpose |
|---|---|---|
| `SUBSTACK_CONTENT_AUTHORIZED` | `false` | Explicitly enables the configured Substack RSS sources when set to `true` |
| `OVERSEAS_STORIES_PRODUCTION_ENABLED` | `false` | Cutover safety gate; enable only after successful E2E and retiring the old scheduler |
| `OVERSEAS_STORIES_INTEGRATION_ENABLED` | `false` | Enables scheduled / push-triggered read-only integration preflight after secrets are configured |

The production job is **disabled by default**. Set the repository variable `OVERSEAS_STORIES_PRODUCTION_ENABLED=true` **only after** GitHub-hosted end-to-end validation, secret configuration, and the old server timer being disabled. Otherwise the scheduled job is skipped and never writes to production. The workflow fails early when required database or model secrets are missing. The AI endpoint must use HTTPS.

### Substack content boundary

Substack collection is disabled unless `SUBSTACK_CONTENT_AUTHORIZED=true` is explicitly configured. Only enable it after reviewing the current platform terms and obtaining the permissions needed for the intended storage and processing of the selected publication content. The adapter does not bypass paywalls. RSS availability alone is not a blanket content licence.

The code licence below does not grant rights to third-party articles, images, names, or other source materials. See `docs/data-rights.md`.

### Automatic read-only integration preflight

The separate `.github/workflows/integration-preflight.yml` runs automatically on relevant `main` pushes and weekly, but only when the repository variable `OVERSEAS_STORIES_INTEGRATION_ENABLED=true`. It verifies the real GitHub Runner's PostgreSQL TLS/read-only query access and the indie.md adapter's network access without AI calls or database writes. It is **not** a complete M2/M4 model-generation E2E, and successful preflight alone cannot authorize production cutover.

## Tests

The CI workflow runs offline unit tests and Python compilation on pushes and pull requests:

```bash
python -m unittest discover -s tests -v
python -m compileall -q src
```

Tests use synthetic fixtures and do not require network access, API keys, or production data.

## Repository contents

- `src/overseas_stories/`: collectors, database repository, extraction, indexing, localization
- `db/`: initial schema and additive M3/M4 migrations
- `tests/`: offline regression tests
- `.github/workflows/`: CI and scheduled pipeline

## Licence

The source code is licensed under the MIT License. Third-party source content and media are not included in that licence. See `LICENSE` and `docs/data-rights.md`.
