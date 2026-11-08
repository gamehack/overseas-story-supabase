# Architecture

## Runtime

The project is a scheduled, stateless worker. GitHub Actions provides the ephemeral Python runtime; Supabase PostgreSQL is the durable store; an HTTPS OpenAI-compatible gateway provides model inference.

No application container or timer is required on a VPS.

## Processing stages

1. **M1 — Collection**: source adapters normalize people and stories while preserving source IDs, URLs, publication dates, and hashes.
2. **M2 — Extraction**: model output is validated against exact source substrings. Invalid evidence is rejected before persistence.
3. **M3 — Editorial index**: deterministic code indexes projects and facts per story, scores editorial usefulness, and preserves human review states.
4. **M4 — Chinese research layer**: selected facts are translated into Chinese. The English source and evidence remain authoritative.

## Data flow

```text
GitHub Actions (weekly cron)
  ├─ indie.md public Git repository
  ├─ authorized RSS feeds (opt-in)
  ├─ HTTPS model gateway
  └─ Supabase PostgreSQL
       ├─ sources / people / stories / story_people
       ├─ projects / facts / editorial_candidates
       └─ story_localizations / fact_localizations
```

## Boundaries

- This project does not publish to social platforms, CMSs, or messaging services.
- The database schema is private and is not exposed through the Supabase Data API.
- Raw third-party content remains in the user's private database; it is not committed to this repository.
- Model calls are bounded separately for M2 and M4.
