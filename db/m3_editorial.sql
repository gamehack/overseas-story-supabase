-- M3 is additive. Do not modify the M1/M2 source archive or CMS schemas.
-- Every project mention is scoped to one story. Identical names from different
-- sources are deliberately NOT treated as the same legal entity or product.

CREATE TABLE IF NOT EXISTS overseas_stories.projects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    story_id uuid NOT NULL REFERENCES overseas_stories.stories(id) ON DELETE CASCADE,
    display_name text NOT NULL,
    evidence text NOT NULL,
    evidence_hash text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (story_id, evidence_hash)
);

CREATE TABLE IF NOT EXISTS overseas_stories.facts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    story_id uuid NOT NULL REFERENCES overseas_stories.stories(id) ON DELETE CASCADE,
    fact_type text NOT NULL CHECK (fact_type IN (
      'startup_products', 'origins', 'motivations', 'struggles', 'setbacks',
      'pivots', 'milestones', 'lessons', 'founder_numbers'
    )),
    value text NOT NULL,
    evidence text NOT NULL,
    evidence_hash text NOT NULL,
    self_stated boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (story_id, fact_type, evidence_hash)
);

CREATE TABLE IF NOT EXISTS overseas_stories.editorial_candidates (
    story_id uuid PRIMARY KEY REFERENCES overseas_stories.stories(id) ON DELETE CASCADE,
    score integer NOT NULL CHECK (score BETWEEN 0 AND 100),
    score_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
    review_status text NOT NULL DEFAULT 'new' CHECK (review_status IN (
      'new', 'shortlisted', 'review_requested', 'handed_off', 'rejected'
    )),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS overseas_facts_story_idx
    ON overseas_stories.facts (story_id, fact_type) WHERE active;
CREATE INDEX IF NOT EXISTS overseas_projects_story_idx
    ON overseas_stories.projects (story_id) WHERE active;
CREATE INDEX IF NOT EXISTS overseas_editorial_score_idx
    ON overseas_stories.editorial_candidates (score DESC, review_status);
-- Existing story_people PK starts with story_id; person profiles join by person_id.
CREATE INDEX IF NOT EXISTS overseas_story_people_person_idx
    ON overseas_stories.story_people (person_id);

-- Extend the existing least-privilege collector account, never CMS or API roles.
ALTER TABLE overseas_stories.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE overseas_stories.facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE overseas_stories.editorial_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON overseas_stories.projects, overseas_stories.facts,
    overseas_stories.editorial_candidates FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON overseas_stories.projects,
    overseas_stories.facts, overseas_stories.editorial_candidates TO overseas_story_runner;

DROP POLICY IF EXISTS overseas_collector_projects ON overseas_stories.projects;
CREATE POLICY overseas_collector_projects ON overseas_stories.projects
    FOR ALL TO overseas_story_runner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS overseas_collector_facts ON overseas_stories.facts;
CREATE POLICY overseas_collector_facts ON overseas_stories.facts
    FOR ALL TO overseas_story_runner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS overseas_collector_editorial ON overseas_stories.editorial_candidates;
CREATE POLICY overseas_collector_editorial ON overseas_stories.editorial_candidates
    FOR ALL TO overseas_story_runner USING (true) WITH CHECK (true);

-- SECURITY INVOKER prevents views from silently bypassing table RLS.
CREATE OR REPLACE VIEW overseas_stories.person_profiles
    WITH (security_invoker = true) AS
SELECT p.id AS person_id, p.source_id, s.name AS source_name,
       p.source_person_id, p.name, p.tagline, p.bio, p.profile_url,
       count(DISTINCT sp.story_id) FILTER (WHERE sp.role = 'founder') AS founder_stories,
       count(DISTINCT sp.story_id) AS total_stories
FROM overseas_stories.people p
JOIN overseas_stories.sources s ON s.id = p.source_id
LEFT JOIN overseas_stories.story_people sp ON sp.person_id = p.id
GROUP BY p.id, s.name;

CREATE OR REPLACE VIEW overseas_stories.case_index
    WITH (security_invoker = true) AS
SELECT st.id AS story_id, st.title, st.source_url, st.published_at,
       so.name AS source_name, ec.score, ec.score_breakdown, ec.review_status,
       (SELECT count(*) FROM overseas_stories.facts f
        WHERE f.story_id = st.id AND f.active) AS facts_count,
       (SELECT count(*) FROM overseas_stories.projects pr
        WHERE pr.story_id = st.id AND pr.active) AS project_mentions,
       (SELECT count(*) FROM overseas_stories.story_people sp
        WHERE sp.story_id = st.id AND sp.role = 'founder') AS founders_count
FROM overseas_stories.stories st
JOIN overseas_stories.sources so ON so.id = st.source_id
JOIN overseas_stories.editorial_candidates ec ON ec.story_id = st.id;

REVOKE ALL ON overseas_stories.person_profiles,
    overseas_stories.case_index FROM PUBLIC, anon, authenticated;
GRANT SELECT ON overseas_stories.person_profiles,
    overseas_stories.case_index TO overseas_story_runner;
