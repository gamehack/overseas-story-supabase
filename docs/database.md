# Database setup

## Existing database

If the project already has the `overseas_stories` schema and M1–M4 tables, do not run the initial schema or migrations again. Point `DATABASE_URL` at the existing database using the current least-privilege login. The application only performs incremental upserts and does not require a data export/import for a code-repository migration.

## New database

Apply these scripts in order using a privileged database session:

1. `db/schema.sql`
2. `db/m3_editorial.sql`
3. `db/m4_localization.sql`

The scripts create a non-login group role named `overseas_story_runner`, grant it only the schema/table privileges required by the collector, and add RLS policies for that role. Do not grant these tables to `anon` or `authenticated`.

Create a dedicated login role and grant membership:

```sql
CREATE ROLE overseas_story_runner_login
  LOGIN INHERIT PASSWORD 'REPLACE_WITH_A_LONG_RANDOM_PASSWORD';
GRANT overseas_story_runner TO overseas_story_runner_login;
```

Use that login in the server-side PostgreSQL connection URL. Do not use a publishable/anon key or expose the database password in source control.

## Connection from GitHub Actions

Use the Supabase session pooler URL if the direct database host is not reachable from the GitHub-hosted runner. Require TLS with `sslmode=require`; prefer `sslmode=verify-full` when the appropriate CA certificate is available in the runner environment.

Store the connection URL as the GitHub Actions secret `DATABASE_URL`. Do not place it in repository variables or workflow YAML.
