import unittest
from dataclasses import replace
from datetime import date

from overseas_stories.indie_md import IndieMDAdapter
from overseas_stories.models import Collection, Person, Story
from overseas_stories.repository import Outcome
from overseas_stories.sync import AiCallBudget, sync_collection

from test_indie_md import FakeGitHub


class MemoryRepository:
    """In-memory fake with DB ids distinct from source-person slugs."""

    def __init__(self):
        self.people = {}  # (source_id, source_person_id) -> Person
        self.person_db_ids = {}  # (source_id, source_person_id) -> db person id
        self.next_person_id = 1
        self.stories = {}  # (source_id, source_item_id) -> Story
        self.story_ids = {}  # (source_id, source_item_id) -> db story id
        self.last_revision = None
        self.story_people = {}  # (source_id, source_item_id) -> [(db_person_id, role)]
        self.upsert_story_calls = []
        self.fail_person_slugs = set()

    def ensure_source(self, key, name, url, kind):
        return 1

    def upsert_person(self, source_id, person):
        if person.source_person_id in self.fail_person_slugs:
            raise RuntimeError("person write failed")
        key = (source_id, person.source_person_id)
        old = self.people.get(key)
        action = "unchanged" if old and old.content_hash == person.content_hash else (
            "updated" if old else "inserted"
        )
        if key in self.person_db_ids:
            db_id = self.person_db_ids[key]
        else:
            db_id = f"db-person-{self.next_person_id}"
            self.next_person_id += 1
            self.person_db_ids[key] = db_id
        self.people[key] = person
        return Outcome(db_id, action)

    def read_story(self, source_id, source_item_id):
        key = (source_id, source_item_id)
        if key not in self.stories:
            return None
        story = self.stories[key]
        return self.story_ids[key], story.content_hash, story.metadata

    def upsert_story(self, source_id, story, person_ids, person_roles, replace_relations=True):
        """person_ids and person_roles are keyed by database person id."""
        self.upsert_story_calls.append(
            (source_id, story.source_item_id, list(person_ids), dict(person_roles), replace_relations)
        )
        key = (source_id, story.source_item_id)
        old = self.stories.get(key)
        content_changed = old is None or old.content_hash != story.content_hash
        ai_changed = old is None or old.metadata != story.metadata
        if old is None:
            story_id = f"db-story-{len(self.stories) + 1}"
            self.story_ids[key] = story_id
            action = "inserted"
        elif content_changed or ai_changed:
            story_id = self.story_ids[key]
            action = "updated"
        else:
            story_id = self.story_ids[key]
            action = "unchanged"
        self.stories[key] = story
        if replace_relations:
            self.story_people[key] = [
                (pid, person_roles.get(pid, "founder")) for pid in person_ids
            ]
        elif story_id is not None:
            existing = dict(self.story_people.get(key, []))
            for pid in person_ids:
                existing[pid] = person_roles.get(pid, "founder")
            self.story_people[key] = list(existing.items())
        return Outcome(story_id, action)

    def mark_checked(self, source_id, revision):
        self.last_revision = revision


class FakeExtractor:
    def __init__(self, configured=True, model="test-model", should_fail=False):
        self.configured = configured
        self.model = model
        self.should_fail = should_fail
        self.calls = 0

    def extract(self, story, source_key):
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("model down")
        from overseas_stories.extraction import ExtractionResult
        founder = Person(
            source_person_id="founder:alice",
            name="Alice Founder",
            profile_url=story.source_url,
            tagline="Founder identified in source story",
            metadata={"identity_basis": "structured_extraction", "source_item_id": story.source_item_id},
            content_hash="founder-hash",
        )
        data = {
            "relevant": True,
            "subject_founders": [{"name": "Alice Founder", "evidence": "I founded X"}],
            "status": "complete",
            "schema_version": 1,
            "model": self.model,
        }
        return ExtractionResult(data, (founder,))


def _pending_substack_story(content_hash="story-hash", ai_status="pending_not_configured", candidate=True):
    author = Person(
        source_person_id="author:newsletter", name="Newsletter Author",
        profile_url="https://example.org/", metadata={}, content_hash="person-hash",
    )
    story = Story(
        source_item_id="item-1",
        title="Founder Story: How I Built X",
        subtitle="",
        source_url="https://pub.substack.com/p/x",
        published_at=date(2026, 11, 2),
        raw_markdown="I founded X in 2024.",
        body_markdown="I founded X in 2024.",
        person_ids=("author:newsletter",),
        person_roles={"author:newsletter": "author"},
        metadata={
            "content_kind": "substack_rss_item",
            "relevance": {"founder_story_candidate": candidate},
            "ai_extraction": {"status": ai_status},
        },
        content_hash=content_hash,
    )
    return Collection(
        (author,), (story,), "rev",
        source_key="substack:demo", source_name="Demo",
        source_url="https://demo.substack.com/feed", source_kind="substack_rss",
    )


class SyncTests(unittest.TestCase):
    def test_twice_is_idempotent_and_story_updates(self):
        collection = IndieMDAdapter(FakeGitHub()).collect()
        repository = MemoryRepository()
        first = sync_collection(collection, repository)
        second = sync_collection(collection, repository)
        self.assertEqual(first.stories, {"inserted": 1})
        self.assertEqual(second.stories, {"unchanged": 1})
        self.assertEqual(len(repository.stories), 1)
        changed_story = replace(collection.stories[0], content_hash="new-hash")
        third = sync_collection(replace(collection, stories=(changed_story,)), repository)
        self.assertEqual(third.stories, {"updated": 1})
        self.assertEqual(repository.story_people[(1, "journey-a")], [("db-person-1", "founder")])

    def test_missing_person_blocks_only_that_story(self):
        collection = IndieMDAdapter(FakeGitHub()).collect()
        story = replace(collection.stories[0], person_ids=("not-present",))
        repository = MemoryRepository()
        report = sync_collection(replace(collection, stories=(story,)), repository)
        self.assertEqual(report.people, {"inserted": 1})
        self.assertFalse(report.stories)
        self.assertEqual(len(report.errors), 1)
        self.assertIsNone(repository.last_revision)


class RolePropagationTests(unittest.TestCase):
    def test_substack_author_stored_as_author_keyed_by_db_id(self):
        author = Person(
            source_person_id="author:newsletter", name="Newsletter Author",
            profile_url="https://example.org/", metadata={}, content_hash="person-hash",
        )
        story = Story(
            source_item_id="item-1",
            title="Founder Story: How I Built X",
            subtitle="",
            source_url="https://pub.substack.com/p/x",
            published_at=date(2026, 11, 2),
            raw_markdown="I founded X.",
            body_markdown="I founded X.",
            person_ids=("author:newsletter",),
            person_roles={"author:newsletter": "author"},
            metadata={
                "source_kind": "substack_rss",
                "relevance": {"founder_story_candidate": True},
                "ai_extraction": {"status": "complete"},
            },
            content_hash="story-hash",
        )
        collection = Collection(
            (author,), (story,), "rev",
            source_key="substack:demo", source_name="Demo",
            source_url="https://demo.substack.com/feed", source_kind="substack_rss",
        )
        repository = MemoryRepository()
        report = sync_collection(collection, repository)
        self.assertFalse(report.errors)
        call = repository.upsert_story_calls[0]
        # DB ids are distinct from slugs; roles keyed by db id.
        self.assertEqual(call[2], ["db-person-1"])
        self.assertEqual(call[3], {"db-person-1": "author"})
        self.assertEqual(call[4], True)
        self.assertEqual(repository.story_people[(1, "item-1")], [("db-person-1", "author")])

    def test_indie_md_defaults_to_founder(self):
        person = Person(
            source_person_id="founder-a", name="Founder A",
            profile_url="https://example.org/", metadata={}, content_hash="person-hash",
        )
        story = Story(
            source_item_id="journey-a",
            title="A Journey",
            subtitle="How a tool came to life",
            source_url="https://indie.md/journeys/journey-a/",
            published_at=date(2026, 11, 3),
            raw_markdown="body",
            body_markdown="body",
            person_ids=("founder-a",),
            person_roles={},
            metadata={"content_kind": "journey"},
            content_hash="story-hash",
        )
        collection = Collection(
            (person,), (story,), "rev",
            source_key="indie.md", source_name="indie.md",
            source_url="https://indie.md/", source_kind="github_markdown",
        )
        repository = MemoryRepository()
        sync_collection(collection, repository)
        self.assertEqual(repository.story_people[(1, "journey-a")], [("db-person-1", "founder")])

    def test_role_updates_even_when_content_hash_unchanged(self):
        author = Person(
            source_person_id="author:newsletter", name="Newsletter Author",
            profile_url="https://example.org/", metadata={}, content_hash="person-hash",
        )
        story = Story(
            source_item_id="item-1",
            title="T",
            subtitle="",
            source_url="https://pub.substack.com/p/x",
            published_at=date(2026, 11, 2),
            raw_markdown="body",
            body_markdown="body",
            person_ids=("author:newsletter",),
            person_roles={"author:newsletter": "author"},
            metadata={"content_kind": "substack_rss_item"},
            content_hash="same-hash",
        )
        collection = Collection(
            (author,), (story,), "rev",
            source_key="substack:demo", source_name="Demo",
            source_url="https://demo.substack.com/feed", source_kind="substack_rss",
        )
        repository = MemoryRepository()
        first = sync_collection(collection, repository)
        self.assertEqual(first.stories, {"inserted": 1})
        changed_role = replace(story, person_roles={"author:newsletter": "founder"})
        second = sync_collection(replace(collection, stories=(changed_role,)), repository)
        self.assertEqual(second.stories, {"unchanged": 1})
        self.assertEqual(repository.story_people[(1, "item-1")], [("db-person-1", "founder")])


class ExtractionWiringTests(unittest.TestCase):
    def test_indie_journey_reuses_original_founder_and_m2_metadata(self):
        repository = MemoryRepository()
        collection = IndieMDAdapter(FakeGitHub()).collect()
        source_slug = collection.stories[0].person_ids[0]
        self.assertEqual(collection.stories[0].metadata["ai_extraction"]["status"], "pending_not_configured")
        extractor = FakeExtractor()
        budget = AiCallBudget(5)
        first = sync_collection(collection, repository, extractor=extractor, ai_budget=budget)
        self.assertFalse(first.errors)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(budget.used, 1)
        self.assertEqual(repository.stories[(1, "journey-a")].metadata["ai_extraction"]["status"], "complete")
        self.assertEqual(list(repository.people), [(1, source_slug)])
        self.assertEqual(repository.story_people[(1, "journey-a")], [("db-person-1", "founder")])
        second = sync_collection(collection, repository, extractor=extractor, ai_budget=budget)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(second.stories, {"unchanged": 1})
        self.assertEqual(budget.used, 1)

    def test_indie_retries_when_budget_available_and_fails_safely(self):
        collection = IndieMDAdapter(FakeGitHub()).collect()
        repository = MemoryRepository()
        budget = AiCallBudget(0)
        extractor = FakeExtractor()
        sync_collection(collection, repository, extractor=extractor, ai_budget=budget)
        self.assertEqual(extractor.calls, 0)
        self.assertEqual(repository.stories[(1, "journey-a")].metadata["ai_extraction"]["status"], "pending_not_configured")
        failed = FakeExtractor(should_fail=True)
        report = sync_collection(collection, repository, extractor=failed, ai_budget=AiCallBudget(1))
        self.assertFalse(report.errors)
        self.assertTrue(report.warnings)
        self.assertEqual(repository.stories[(1, "journey-a")].metadata["ai_extraction"]["status"], "error")
        self.assertEqual(repository.story_people[(1, "journey-a")], [("db-person-1", "founder")])
        recovered = FakeExtractor()
        sync_collection(collection, repository, extractor=recovered, ai_budget=AiCallBudget(1))
        self.assertEqual(recovered.calls, 1)
        self.assertEqual(repository.stories[(1, "journey-a")].metadata["ai_extraction"]["status"], "complete")

    def test_extractor_runs_for_relevant_pending_substack_story(self):
        extractor = FakeExtractor()
        collection = _pending_substack_story()
        repository = MemoryRepository()
        report = sync_collection(collection, repository, extractor=extractor)
        self.assertEqual(extractor.calls, 1)
        self.assertFalse(report.errors)
        synced = repository.stories[(1, "item-1")]
        self.assertEqual(synced.metadata["ai_extraction"]["status"], "complete")
        self.assertEqual(synced.metadata["ai_extraction"]["model"], "test-model")
        self.assertIn((1, "founder:alice"), repository.people)
        self.assertEqual(
            sorted(repository.story_people[(1, "item-1")]),
            sorted([("db-person-1", "author"), ("db-person-2", "founder")]),
        )

    def test_extractor_not_called_for_non_relevant_story(self):
        extractor = FakeExtractor()
        collection = _pending_substack_story(candidate=False)
        repository = MemoryRepository()
        sync_collection(collection, repository, extractor=extractor)
        self.assertEqual(extractor.calls, 0)
        sync_collection(collection, repository, extractor=extractor)
        self.assertEqual(extractor.calls, 0)

    def test_extractor_called_once_across_two_identical_polls(self):
        extractor = FakeExtractor()
        repository = MemoryRepository()
        first = sync_collection(_pending_substack_story(), repository, extractor=extractor)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(first.stories, {"inserted": 1})
        # Same content hash, fresh Story object: stored ai complete -> skip re-extraction.
        second = sync_collection(_pending_substack_story(content_hash="story-hash"), repository, extractor=extractor)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(second.stories, {"unchanged": 1})
        synced = repository.stories[(1, "item-1")]
        self.assertEqual(synced.metadata["ai_extraction"]["status"], "complete")
        self.assertEqual(
            sorted(repository.story_people[(1, "item-1")]),
            sorted([("db-person-1", "author"), ("db-person-2", "founder")]),
        )

    def test_unconfigured_second_run_preserves_founders_and_metadata(self):
        extractor = FakeExtractor()
        repository = MemoryRepository()
        first = sync_collection(_pending_substack_story(), repository, extractor=extractor)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(
            sorted(repository.story_people[(1, "item-1")]),
            sorted([("db-person-1", "author"), ("db-person-2", "founder")]),
        )
        # Later run with no extractor configured: content unchanged, preserve enrichment.
        unconfigured = FakeExtractor(configured=False)
        second = sync_collection(_pending_substack_story(content_hash="story-hash"), repository, extractor=unconfigured)
        self.assertEqual(extractor.calls, 1)  # no additional paid call
        self.assertEqual(second.stories, {"unchanged": 1})
        self.assertEqual(repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"], "complete")
        self.assertEqual(
            sorted(repository.story_people[(1, "item-1")]),
            sorted([("db-person-1", "author"), ("db-person-2", "founder")]),
        )

    def test_pending_to_complete_retry_same_hash(self):
        repository = MemoryRepository()
        # First run unconfigured: pending, no founder.
        first = sync_collection(_pending_substack_story(), repository, extractor=FakeExtractor(configured=False))
        self.assertEqual(repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"], "pending_not_configured")
        self.assertEqual(len(repository.story_people[(1, "item-1")]), 1)  # author only
        # Second run configured, same hash: retry pending -> complete, add founder.
        extractor = FakeExtractor()
        second = sync_collection(_pending_substack_story(content_hash="story-hash"), repository, extractor=extractor)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(second.stories, {"updated": 1})  # ai metadata changed
        self.assertEqual(repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"], "complete")
        self.assertEqual(
            sorted(repository.story_people[(1, "item-1")]),
            sorted([("db-person-1", "author"), ("db-person-2", "founder")]),
        )

    def test_extractor_not_repeated_on_unchanged_complete_record(self):
        extractor = FakeExtractor()
        collection = _pending_substack_story(ai_status="complete")
        repository = MemoryRepository()
        sync_collection(collection, repository, extractor=extractor)
        self.assertEqual(extractor.calls, 0)

    def test_complete_irrelevant_is_also_persistently_idempotent(self):
        extractor = FakeExtractor()
        collection = _pending_substack_story(ai_status="complete_irrelevant")
        repository = MemoryRepository()
        sync_collection(collection, repository, extractor=extractor)
        sync_collection(collection, repository, extractor=extractor)
        self.assertEqual(extractor.calls, 0)

    def test_failed_founder_upsert_isolated_and_raw_story_still_saved(self):
        extractor = FakeExtractor()
        collection = _pending_substack_story()
        repository = MemoryRepository()
        repository.fail_person_slugs.add("founder:alice")
        report = sync_collection(collection, repository, extractor=extractor)
        self.assertTrue(any("founder founder:alice" in error for error in report.errors))
        self.assertEqual(report.stories, {"inserted": 1})
        self.assertEqual(
            repository.story_people[(1, "item-1")],
            [("db-person-1", "author")],
        )

    def test_extractor_failure_does_not_corrupt_raw_ingestion(self):
        extractor = FakeExtractor(should_fail=True)
        collection = _pending_substack_story()
        repository = MemoryRepository()
        report = sync_collection(collection, repository, extractor=extractor)
        self.assertEqual(extractor.calls, 1)
        synced = repository.stories[(1, "item-1")]
        self.assertEqual(synced.body_markdown, "I founded X in 2024.")
        self.assertEqual(synced.metadata["ai_extraction"]["status"], "error")
        self.assertEqual(synced.metadata["ai_extraction"]["finish_reason"], "unknown")
        self.assertEqual(synced.metadata["ai_extraction"]["token_usage"], {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        })
        self.assertFalse(synced.metadata["ai_extraction"]["evidence_check_passed"])
        self.assertNotIn((1, "founder:alice"), repository.people)
        self.assertTrue(any("ai extraction failed" in w for w in report.warnings))
        self.assertEqual(report.stories.get("inserted", 0), 1)

    def test_unconfigured_extractor_leaves_pending_no_founder(self):
        extractor = FakeExtractor(configured=False)
        collection = _pending_substack_story()
        repository = MemoryRepository()
        report = sync_collection(collection, repository, extractor=extractor)
        self.assertEqual(extractor.calls, 0)
        self.assertEqual(
            repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"],
            "pending_not_configured",
        )
        self.assertNotIn((1, "founder:alice"), repository.people)
        self.assertFalse(report.errors)

    def test_global_budget_caps_calls_across_collections_and_leaves_pending(self):
        extractor = FakeExtractor()
        budget = AiCallBudget(1)
        first_repository = MemoryRepository()
        second_repository = MemoryRepository()
        first = _pending_substack_story()
        second = replace(
            _pending_substack_story(),
            source_key="substack:second",
            source_name="Second",
        )
        sync_collection(first, first_repository, extractor=extractor, ai_budget=budget)
        sync_collection(second, second_repository, extractor=extractor, ai_budget=budget)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(budget.used, 1)
        self.assertEqual(
            first_repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"],
            "complete",
        )
        self.assertEqual(
            second_repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"],
            "pending_not_configured",
        )
        self.assertEqual(
            second_repository.story_people[(1, "item-1")],
            [("db-person-1", "author")],
        )

    def test_failed_model_call_consumes_budget(self):
        extractor = FakeExtractor(should_fail=True)
        budget = AiCallBudget(1)
        first_repository = MemoryRepository()
        second_repository = MemoryRepository()
        sync_collection(_pending_substack_story(), first_repository, extractor=extractor, ai_budget=budget)
        sync_collection(_pending_substack_story(), second_repository, extractor=extractor, ai_budget=budget)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(budget.used, 1)
        self.assertEqual(
            second_repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"],
            "pending_not_configured",
        )

    def test_rescored_old_skip_becomes_pending_when_budget_is_zero(self):
        repository = MemoryRepository()
        previously_skipped = _pending_substack_story(ai_status="skipped_irrelevant", candidate=False)
        sync_collection(previously_skipped, repository, extractor=FakeExtractor(configured=False))
        self.assertEqual(repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"], "skipped_irrelevant")

        rescored = _pending_substack_story(candidate=True)
        extractor = FakeExtractor()
        zero_budget = AiCallBudget(0)
        report = sync_collection(rescored, repository, extractor=extractor, ai_budget=zero_budget)
        self.assertEqual(report.stories, {"updated": 1})
        self.assertEqual(extractor.calls, 0)
        self.assertEqual(repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"], "pending_not_configured")
        self.assertEqual(repository.story_people[(1, "item-1")], [("db-person-1", "author")])

        sync_collection(rescored, repository, extractor=extractor, ai_budget=AiCallBudget(1))
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"], "complete")

    def test_rescored_old_skip_becomes_pending_when_model_unconfigured(self):
        repository = MemoryRepository()
        sync_collection(_pending_substack_story(ai_status="skipped_irrelevant", candidate=False),
                        repository, extractor=FakeExtractor(configured=False))
        sync_collection(_pending_substack_story(candidate=True), repository,
                        extractor=FakeExtractor(configured=False))
        self.assertEqual(repository.stories[(1, "item-1")].metadata["ai_extraction"]["status"], "pending_not_configured")


if __name__ == "__main__":
    unittest.main()
