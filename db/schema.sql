-- Initial schema (not applied automatically). Apply only to the explicitly chosen Supabase project.
-- The schema is deliberately private and not exposed through the Supabase Data API.
CREATE SCHEMA IF NOT EXISTS overseas_stories;
REVOKE ALL ON SCHEMA overseas_stories FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS overseas_stories.sources (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key text NOT NULL UNIQUE,
    name text NOT NULL,
    url text NOT NULL,
    kind text NOT NULL,
    last_revision text,
    last_checked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS overseas_stories.people (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id bigint NOT NULL REFERENCES overseas_stories.sources(id),
    source_person_id text NOT NULL,
    name text NOT NULL,
    tagline text NOT NULL DEFAULT '',
    bio text NOT NULL DEFAULT '',
    profile_url text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, source_person_id)
);

CREATE TABLE IF NOT EXISTS overseas_stories.stories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id bigint NOT NULL REFERENCES overseas_stories.sources(id),
    source_item_id text NOT NULL,
    title text NOT NULL,
    subtitle text NOT NULL DEFAULT '',
    source_url text NOT NULL,
    published_at date NOT NULL,
    raw_markdown text NOT NULL,
    body_markdown text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_hash text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, source_item_id)
);

CREATE INDEX IF NOT EXISTS stories_published_at_idx
    ON overseas_stories.stories (published_at DESC);

CREATE TABLE IF NOT EXISTS overseas_stories.story_people (
    story_id uuid NOT NULL REFERENCES overseas_stories.stories(id) ON DELETE CASCADE,
    person_id uuid NOT NULL REFERENCES overseas_stories.people(id),
    role text NOT NULL DEFAULT 'founder',
    PRIMARY KEY (story_id, person_id)
);

-- Defense in depth: these are private ingestion tables, with no public policies.
ALTER TABLE overseas_stories.sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE overseas_stories.people ENABLE ROW LEVEL SECURITY;
ALTER TABLE overseas_stories.stories ENABLE ROW LEVEL SECURITY;
ALTER TABLE overseas_stories.story_people ENABLE ROW LEVEL SECURITY;

-- Supabase's API roles must not read or mutate the private source archive.
REVOKE ALL ON ALL TABLES IN SCHEMA overseas_stories FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA overseas_stories FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA overseas_stories
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;



-- A non-login group role keeps database privileges separate from credentials.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overseas_story_runner') THEN
        CREATE ROLE overseas_story_runner NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA overseas_stories TO overseas_story_runner;
GRANT SELECT, INSERT, UPDATE ON overseas_stories.sources,
    overseas_stories.people, overseas_stories.stories TO overseas_story_runner;
GRANT SELECT, INSERT, UPDATE, DELETE ON overseas_stories.story_people
    TO overseas_story_runner;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA overseas_stories
    TO overseas_story_runner;

DROP POLICY IF EXISTS overseas_story_runner_sources ON overseas_stories.sources;
CREATE POLICY overseas_story_runner_sources ON overseas_stories.sources
    FOR ALL TO overseas_story_runner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS overseas_story_runner_people ON overseas_stories.people;
CREATE POLICY overseas_story_runner_people ON overseas_stories.people
    FOR ALL TO overseas_story_runner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS overseas_story_runner_stories ON overseas_stories.stories;
CREATE POLICY overseas_story_runner_stories ON overseas_stories.stories
    FOR ALL TO overseas_story_runner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS overseas_story_runner_story_people ON overseas_stories.story_people;
CREATE POLICY overseas_story_runner_story_people ON overseas_stories.story_people
    FOR ALL TO overseas_story_runner USING (true) WITH CHECK (true);

ALTER DEFAULT PRIVILEGES IN SCHEMA overseas_stories
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO overseas_story_runner;
ALTER DEFAULT PRIVILEGES IN SCHEMA overseas_stories
    GRANT USAGE, SELECT ON SEQUENCES TO overseas_story_runner;
