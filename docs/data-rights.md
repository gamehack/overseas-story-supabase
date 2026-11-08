# Data rights and source boundaries

The MIT licence in this repository covers this project's original source code only. It does not grant rights to third-party stories, articles, photographs, logos, names, or other source materials.

## indie.md

The adapter reads public Markdown files from the upstream repository. Before redistributing any upstream story text or media, check the upstream repository's current licence and the individual contributor permissions. A code licence for the upstream project should not be assumed to grant every right in every contributed work.

## Substack RSS

Substack ingestion is disabled by default. It requires the explicit runtime setting `SUBSTACK_CONTENT_AUTHORIZED=true`. Only enable it when you have reviewed the current platform terms and have the permissions required for the selected publications and the intended storage and AI processing.

- RSS availability is not a blanket content licence.
- The adapter does not bypass paywalls or fetch subscriber-only archives.
- Do not republish full articles or media without the required rights.
- Keep source attribution and canonical URLs with every imported item.

## Production data

Do not commit production database dumps, original articles, AI evidence records, Chinese research records, credentials, or model responses to this repository. Keep these in the private database and restrict access to the runner role.

This document is an engineering safeguard, not legal advice.
