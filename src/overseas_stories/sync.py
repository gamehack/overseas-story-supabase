"""Synchronize source records while preserving provenance and idempotency."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from .models import Collection, Person, Story
from .repository import Outcome


class Repository(Protocol):
    def ensure_source(self, key: str, name: str, url: str, kind: str) -> int: ...

    def upsert_person(self, source_id: int, person: Person) -> Outcome: ...

    def read_story(
        self, source_id: int, source_item_id: str
    ) -> Optional[tuple[Any, Any, dict]]: ...

    def upsert_story(
        self,
        source_id: int,
        story: Story,
        person_ids: list[str],
        person_roles: dict[str, str],
        replace_relations: bool = True,
    ) -> Outcome: ...

    def mark_checked(self, source_id: int, revision: str) -> None: ...


class Extractor(Protocol):
    """Optional structured extraction client; duck-typed so tests can substitute fakes."""

    configured: bool
    model: str

    def extract(self, story: Story, source_key: str) -> Any: ...


@dataclass(frozen=True)
class SyncReport:
    revision: str
    people: dict[str, int]
    stories: dict[str, int]
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass
class AiCallBudget:
    """A mutable per-process budget shared by every collection in one CLI run."""

    limit: int
    used: int = 0

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError("AI call limit must be non-negative")

    def try_acquire(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


_AI_PENDING_STATUSES = ("pending_not_configured", "pending", "error")
_AI_COMPLETE_STATUSES = ("complete", "complete_irrelevant")


def _needs_ai_extraction(collection: Collection, story: Story) -> bool:
    """Substack candidates and indie.md journeys share one bounded extractor."""
    metadata = story.metadata if isinstance(story.metadata, dict) else {}
    if collection.source_kind == "github_markdown":
        if collection.source_key != "indie.md" or metadata.get("content_kind") != "journey":
            return False
        ai = metadata.get("ai_extraction")
        # Existing indie.md records predate M2 and have no extraction metadata.
        return not isinstance(ai, dict) or ai.get("status") in _AI_PENDING_STATUSES
    if collection.source_kind != "substack_rss":
        return False
    relevance = metadata.get("relevance", {})
    if not isinstance(relevance, dict) or relevance.get("founder_story_candidate") is not True:
        return False
    ai = metadata.get("ai_extraction", {})
    if not isinstance(ai, dict):
        return False
    return ai.get("status") in _AI_PENDING_STATUSES


def sync_collection(
    collection: Collection,
    repository: Repository,
    extractor: Optional[Extractor] = None,
    ai_budget: Optional[AiCallBudget] = None,
) -> SyncReport:
    source_id = repository.ensure_source(
        collection.source_key, collection.source_name, collection.source_url, collection.source_kind
    )
    people_counts: Counter[str] = Counter()
    story_counts: Counter[str] = Counter()
    errors = list(collection.errors)
    warnings: list[str] = []
    people_by_slug: dict[str, str] = {}

    for person in collection.people:
        try:
            result = repository.upsert_person(source_id, person)
            people_by_slug[person.source_person_id] = result.id
            people_counts[result.action] += 1
        except Exception as exc:
            errors.append(f"person {person.source_person_id}: {type(exc).__name__}: {exc}")

    for story in collection.stories:
        missing = [slug for slug in story.person_ids if slug not in people_by_slug]
        if missing:
            errors.append(f"story {story.source_item_id}: missing people {', '.join(missing)}")
            continue

        # Read-only lookup of persisted state before touching this item.
        persisted = repository.read_story(source_id, story.source_item_id)
        persisted_hash = persisted[1] if persisted else None
        persisted_meta = persisted[2] if persisted and isinstance(persisted[2], dict) else {}
        persisted_ai = persisted_meta.get("ai_extraction", {})
        content_unchanged = persisted is not None and persisted_hash == story.content_hash
        ai_complete = (
            isinstance(persisted_ai, dict)
            and persisted_ai.get("status") in _AI_COMPLETE_STATUSES
        )

        story_to_sync = story
        run_extraction = False
        replace_relations = True

        eligible_for_ai = _needs_ai_extraction(collection, story)
        if extractor is not None and extractor.configured and eligible_for_ai:
            if content_unchanged and ai_complete:
                # Stored record already enriched; skip paid re-extraction, preserve it.
                run_extraction = False
                replace_relations = False
                # Mirror the stored complete extraction so an unchanged story is not
                # rewritten or re-marked on every poll.
                metadata = dict(story.metadata)
                metadata["ai_extraction"] = dict(persisted_ai)
                story_to_sync = replace(story, metadata=metadata)
            elif ai_budget is not None and not ai_budget.try_acquire():
                # A global run budget is exhausted. Leave this record pending so
                # a future weekly/manual run can process it without losing data.
                if content_unchanged:
                    replace_relations = False
                    metadata = dict(story.metadata)
                    if (isinstance(persisted_ai, dict) and persisted_ai
                            and not (eligible_for_ai and persisted_ai.get("status") == "skipped_irrelevant")):
                        # v1 may have skipped a story now eligible under v2.
                        # Never preserve an obsolete skip just because AI is capped.
                        metadata["ai_extraction"] = dict(persisted_ai)
                    story_to_sync = replace(story, metadata=metadata)
            else:
                # New, changed, or pending/error retry: (re)extract.
                run_extraction = True
                replace_relations = True
        else:
            # Unconfigured, or not an eligible story: preserve persisted
            # enrichment and relations when content is unchanged.
            if content_unchanged:
                replace_relations = False
                # Do not downgrade a stored complete/pending extraction to "pending_not_configured".
                preserved_ai = dict(persisted_ai) if isinstance(persisted_ai, dict) else {}
                metadata = dict(story.metadata)
                if preserved_ai and not (eligible_for_ai and preserved_ai.get("status") == "skipped_irrelevant"):
                    # Newly eligible stories remain pending if AI is unconfigured.
                    metadata["ai_extraction"] = preserved_ai
                story_to_sync = replace(story, metadata=metadata)

        if run_extraction:
            try:
                result = extractor.extract(story, collection.source_key)
                # indie.md's frontmatter already owns the source-scoped founder
                # identity.  Do not mint a second model-derived profile for it.
                founders = () if collection.source_key == "indie.md" else (
                    result.founders if hasattr(result, "founders") else ()
                )
                saved_founders: list[Person] = []
                for founder in founders:
                    try:
                        presult = repository.upsert_person(source_id, founder)
                        people_counts[presult.action] += 1
                        people_by_slug[founder.source_person_id] = presult.id
                        saved_founders.append(founder)
                    except Exception as exc:
                        errors.append(f"founder {founder.source_person_id}: {type(exc).__name__}: {exc}")
                        continue
                new_ids = list(story.person_ids)
                new_roles = dict(story.person_roles)
                # Never add a relation for a founder whose person row failed.
                for founder in saved_founders:
                    pid = founder.source_person_id
                    if pid not in new_ids:
                        new_ids.append(pid)
                    new_roles[pid] = "founder"
                metadata = dict(story.metadata)
                metadata["ai_extraction"] = result.data if hasattr(result, "data") else {}
                story_to_sync = replace(
                    story,
                    person_ids=tuple(new_ids),
                    person_roles=new_roles,
                    metadata=metadata,
                )
            except Exception as exc:
                # Model failure must not corrupt raw RSS ingestion.
                safe_message = getattr(exc, "safe_message", "structured extraction failed")
                token_usage = getattr(exc, "token_usage", None)
                if not isinstance(token_usage, dict):
                    token_usage = {
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                    }
                metadata = dict(story.metadata)
                metadata["ai_extraction"] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {safe_message}",
                    "schema_version": getattr(extractor, "schema_version", 2),
                    "extractor_version": getattr(extractor, "extractor_version", "2"),
                    "model": getattr(extractor, "model", None),
                    "finish_reason": getattr(exc, "finish_reason", "unknown"),
                    "extracted_at": getattr(
                        exc, "extracted_at", datetime.now(timezone.utc).isoformat()
                    ),
                    "token_usage": token_usage,
                    "evidence_check_passed": False,
                }
                story_to_sync = replace(story, metadata=metadata)
                warnings.append(
                    f"story {story.source_item_id}: ai extraction failed: "
                    f"{type(exc).__name__}: {safe_message}"
                )

        # Convert slug-keyed inputs to database person ids; roles follow.
        db_person_ids = [people_by_slug[slug] for slug in story_to_sync.person_ids]
        db_person_roles = {
            people_by_slug[slug]: story_to_sync.person_roles.get(slug, "founder")
            for slug in story_to_sync.person_ids
        }

        try:
            result = repository.upsert_story(
                source_id,
                story_to_sync,
                db_person_ids,
                db_person_roles,
                replace_relations=replace_relations,
            )
            story_counts[result.action] += 1
        except Exception as exc:
            errors.append(f"story {story_to_sync.source_item_id}: {type(exc).__name__}: {exc}")

    # A partial failure should not mark this revision as fully processed.
    if not errors:
        repository.mark_checked(source_id, collection.revision)
    return SyncReport(
        collection.revision,
        dict(people_counts),
        dict(story_counts),
        tuple(errors),
        tuple(warnings),
    )
