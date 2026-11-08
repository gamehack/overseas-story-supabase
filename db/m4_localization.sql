-- M4: additive Chinese reading layer for evidence-backed founder cases.
-- The English source archive, M2 extraction and M3 facts remain authoritative.
-- This private schema is not published by the Supabase Data API.

CREATE TABLE IF NOT EXISTS overseas_stories.story_localizations (
    story_id uuid NOT NULL REFERENCES overseas_stories.stories(id) ON DELETE CASCADE,
    locale text NOT NULL DEFAULT 'zh-CN' CHECK (locale = 'zh-CN'),
    title_zh text NOT NULL,
    synopsis_zh text NOT NULL,
    outline_zh jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_hash text NOT NULL,
    model text NOT NULL,
    prompt_version text NOT NULL,
    token_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    localized_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (story_id, locale),
    CHECK (char_length(title_zh) BETWEEN 2 AND 160),
    CHECK (char_length(synopsis_zh) BETWEEN 300 AND 500),
    CHECK (jsonb_typeof(outline_zh) = 'object')
);

CREATE TABLE IF NOT EXISTS overseas_stories.fact_localizations (
    fact_id uuid NOT NULL REFERENCES overseas_stories.facts(id) ON DELETE CASCADE,
    locale text NOT NULL DEFAULT 'zh-CN' CHECK (locale = 'zh-CN'),
    value_zh text NOT NULL,
    source_hash text NOT NULL,
    model text NOT NULL,
    prompt_version text NOT NULL,
    localized_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fact_id, locale),
    CHECK (char_length(value_zh) BETWEEN 1 AND 500)
);

ALTER TABLE overseas_stories.story_localizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE overseas_stories.fact_localizations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON overseas_stories.story_localizations,
    overseas_stories.fact_localizations FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON overseas_stories.story_localizations,
    overseas_stories.fact_localizations TO overseas_story_runner;

DROP POLICY IF EXISTS overseas_collector_story_localizations ON overseas_stories.story_localizations;
CREATE POLICY overseas_collector_story_localizations
    ON overseas_stories.story_localizations FOR ALL TO overseas_story_runner
    USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS overseas_collector_fact_localizations ON overseas_stories.fact_localizations;
CREATE POLICY overseas_collector_fact_localizations
    ON overseas_stories.fact_localizations FOR ALL TO overseas_story_runner
    USING (true) WITH CHECK (true);

-- Read-only bilingual view. Joining active source facts ensures that an old
-- translation is never presented as current after its source fact changes.
CREATE OR REPLACE VIEW overseas_stories.localized_facts
    WITH (security_invoker = true) AS
SELECT f.story_id, f.id AS fact_id, f.fact_type, f.value AS value_en,
       f.evidence AS evidence_en, f.self_stated, l.value_zh, l.locale
FROM overseas_stories.facts f
JOIN overseas_stories.fact_localizations l ON l.fact_id=f.id
WHERE f.active AND l.source_hash = encode(
  sha256(convert_to(f.fact_type || E'\n' || f.value || E'\n' || f.evidence, 'UTF8')), 'hex');

REVOKE ALL ON overseas_stories.localized_facts FROM PUBLIC, anon, authenticated;
GRANT SELECT ON overseas_stories.localized_facts TO overseas_story_runner;
