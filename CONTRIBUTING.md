# Contributing

Thanks for considering a contribution.

## Scope

Contributions should improve source adapters, provenance, evidence validation, database safety, tests, or GitHub Actions reliability. This repository does not accept publishing integrations or production data.

## Before opening a pull request

1. Keep changes narrowly scoped.
2. Add or update offline tests using synthetic fixtures.
3. Do not include real articles, personal data, production IDs, logs, database dumps, credentials, or API responses.
4. Do not weaken evidence validation, database RLS, or the explicit Substack authorization gate.
5. Run:

```bash
python -m pip install .
python -m unittest discover -s tests -v
python -m compileall -q src
```

## Pull requests

Explain the problem, implementation, tests, and any database migration implications. Schema changes must be additive where practical and must not expose the private schema through the Supabase Data API.
