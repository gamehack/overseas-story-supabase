"""M3: source-scoped case index and evidence-backed facts.

This is deliberately independent of the scheduled RSS collector. Preview is
read-only; --apply and --request-review are explicit actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any


FACT_TYPES = (
    "startup_products", "origins", "motivations", "struggles", "setbacks",
    "pivots", "milestones", "lessons", "founder_numbers",
)
FACT_LABELS = {
    "startup_products": "项目与产品", "origins": "创业背景", "motivations": "创业动机",
    "struggles": "困难", "setbacks": "挫折", "pivots": "关键转折",
    "milestones": "里程碑", "lessons": "经验教训", "founder_numbers": "创始人自述数字",
}



@dataclass(frozen=True)
class Fact:
    kind: str
    value: str
    evidence: str
    evidence_hash: str
    self_stated: bool


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def extract_facts(ai: Any, body: str) -> list[Fact]:
    """Re-check exact evidence in the stored body before indexing AI output."""
    if not isinstance(ai, dict) or ai.get("status") != "complete":
        return []
    seen: set[tuple[str, str]] = set()
    facts: list[Fact] = []
    for kind in FACT_TYPES:
        values = ai.get(kind, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            value, evidence = item.get("value"), item.get("evidence")
            if not isinstance(value, str) or not isinstance(evidence, str):
                continue
            if not value.strip() or not evidence.strip() or evidence not in body:
                continue
            key = (kind, _hash(evidence))
            if key in seen:
                continue
            seen.add(key)
            facts.append(Fact(kind, value.strip(), evidence, key[1], item.get("self_stated") is True))
    return facts


def score_story(ai: Any, facts: list[Fact], founder_count: int, source_url: str) -> dict[str, int]:
    """Transparent 100-point editorial heuristic, not a truth or revenue rating."""
    if not isinstance(ai, dict) or ai.get("status") != "complete" or ai.get("relevant") is not True:
        return {"total": 0, "evidence": 0, "story_arc": 0, "actionability": 0, "specificity": 0, "provenance": 0}
    kinds = {fact.kind for fact in facts}
    story_arc = sum(6 for k in ("origins", "motivations", "struggles", "setbacks", "pivots") if k in kinds)
    actionability = (10 if "milestones" in kinds else 0) + (10 if "lessons" in kinds else 0) + (5 if "startup_products" in kinds else 0)
    specificity = (10 if "founder_numbers" in kinds else 0) + (5 if founder_count > 0 else 0)
    provenance = 10 if source_url.startswith("https://") and ai.get("evidence_check_passed") is True else 0
    evidence = 20 if facts and ai.get("evidence_check_passed") is True else 0
    result = {"evidence": evidence, "story_arc": story_arc, "actionability": actionability,
              "specificity": specificity, "provenance": provenance}
    result["total"] = sum(result.values())
    return result


def make_case(story: dict[str, Any]) -> dict[str, Any] | None:
    metadata = story.get("metadata") or {}
    ai = metadata.get("ai_extraction") if isinstance(metadata, dict) else None
    if not isinstance(ai, dict) or ai.get("status") != "complete" or ai.get("relevant") is not True:
        return None
    facts = extract_facts(ai, story["body_markdown"])
    if not facts:
        return None
    score = score_story(ai, facts, int(story.get("founder_count") or 0), story["source_url"])
    project_mentions = [f for f in facts if f.kind == "startup_products"]
    return {"story_id": str(story["id"]), "title": story["title"],
            "source_url": story["source_url"], "score": score, "facts": facts,
            "projects": project_mentions}


def _db(dsn: str):
    if not dsn or "CHANGE_ME" in dsn:
        raise ValueError("DATABASE_URL must be set in the private runtime environment")
    import psycopg
    return psycopg.connect(dsn, connect_timeout=15)


def _load_stories(conn: Any, story_id: str | None = None):
    query = """
    SELECT st.id, st.title, st.body_markdown, st.source_url, st.metadata,
           count(sp.person_id) FILTER (WHERE sp.role='founder') AS founder_count
    FROM overseas_stories.stories st
    LEFT JOIN overseas_stories.story_people sp ON sp.story_id=st.id
    WHERE st.metadata->'ai_extraction'->>'status'='complete'
    """
    params: tuple[Any, ...] = ()
    if story_id is not None:
        query += " AND st.id = %s::uuid"
        params = (story_id,)
    query += " GROUP BY st.id ORDER BY st.published_at DESC, st.id"
    for row in conn.execute(query, params).fetchall():
        yield dict(zip(("id", "title", "body_markdown", "source_url", "metadata", "founder_count"), row))


def _upsert_case(conn: Any, case: dict[str, Any]) -> None:
    from psycopg.types.json import Jsonb
    sid = case["story_id"]
    # Soft-deactivate stale facts instead of deleting the historical extraction.
    conn.execute("UPDATE overseas_stories.facts SET active=false WHERE story_id=%s AND active", (sid,))
    conn.execute("UPDATE overseas_stories.projects SET active=false WHERE story_id=%s AND active", (sid,))
    for fact in case["facts"]:
        conn.execute("""
            INSERT INTO overseas_stories.facts
              (story_id, fact_type, value, evidence, evidence_hash, self_stated, active)
            VALUES (%s,%s,%s,%s,%s,%s,true)
            ON CONFLICT (story_id, fact_type, evidence_hash)
            DO UPDATE SET value=excluded.value, evidence=excluded.evidence,
               self_stated=excluded.self_stated, active=true, updated_at=now()
        """, (sid, fact.kind, fact.value, fact.evidence, fact.evidence_hash, fact.self_stated))
    for project in case["projects"]:
        conn.execute("""
            INSERT INTO overseas_stories.projects (story_id, display_name, evidence, evidence_hash, active)
            VALUES (%s,%s,%s,%s,true)
            ON CONFLICT (story_id, evidence_hash)
            DO UPDATE SET display_name=excluded.display_name,
              evidence=excluded.evidence, active=true, updated_at=now()
        """, (sid, project.value, project.evidence, project.evidence_hash))
    conn.execute("""
        INSERT INTO overseas_stories.editorial_candidates (story_id, score, score_breakdown)
        VALUES (%s,%s,%s)
        ON CONFLICT (story_id) DO UPDATE SET score=excluded.score,
          score_breakdown=excluded.score_breakdown, updated_at=now()
    """, (sid, case["score"]["total"], Jsonb(case["score"])))


def index_stories(dsn: str, apply: bool = False, story_id: str | None = None) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    with _db(dsn) as conn:
        stories = list(_load_stories(conn, story_id))
        for story in stories:
            case = make_case(story)
            if case is None:
                reports.append({"story_id": str(story["id"]), "status": "skipped_no_valid_evidence"})
                continue
            reports.append({"story_id": case["story_id"], "score": case["score"]["total"],
                            "facts": len(case["facts"]), "projects": len(case["projects"])})
            if apply:
                with conn.transaction():
                    _upsert_case(conn, case)
        if not apply:
            conn.rollback()
    return {"mode": "apply" if apply else "preview", "count": len(reports), "items": reports}


def request_review(dsn: str, story_id: str) -> dict[str, str]:
    """A human selects a case for editorial review; no external channel calls."""
    with _db(dsn) as conn:
        row = conn.execute("""
            UPDATE overseas_stories.editorial_candidates
               SET review_status='review_requested', updated_at=now()
             WHERE story_id=%s::uuid AND review_status IN ('new','shortlisted')
         RETURNING story_id
        """, (story_id,)).fetchone()
        if row is None:
            raise ValueError("Candidate does not exist or is not eligible for review")
    return {"story_id": story_id, "status": "review_requested"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build source-backed editorial case index")
    parser.add_argument("--apply", action="store_true", help="Write M3 indexing records; default is preview")
    parser.add_argument("--story-id", help="Restrict indexing to a single UUID")
    parser.add_argument("--request-review", metavar="STORY_UUID", help="Manually select one case for review")
    args = parser.parse_args(argv)
    if args.request_review and (args.apply or args.story_id):
        parser.error("Review actions cannot be combined with --apply or --story-id")
    dsn = os.getenv("DATABASE_URL", "")
    if args.request_review:
        result = request_review(dsn, args.request_review)
    else:
        result = index_stories(dsn, args.apply, args.story_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
