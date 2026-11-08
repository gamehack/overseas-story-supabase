# Security policy

Do not report credentials, private source content, production database records, or exploitable details in public issues.

Use GitHub's private vulnerability reporting for this repository when available. Include a concise impact description and reproducible steps without attaching production data.

Secrets must be stored in GitHub Actions Secrets or a dedicated secret manager. Never commit `.env` files, connection strings, database dumps, private keys, or model credentials.

The scheduled runner uses a least-privilege PostgreSQL login. The `overseas_stories` schema is private and must not be granted to Supabase `anon` or `authenticated` roles.
