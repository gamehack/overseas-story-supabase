import unittest
from datetime import date

from overseas_stories.indie_md import (
    ContentError,
    IndieMDAdapter,
    parse_frontmatter,
    parse_person,
    parse_story,
)


PERSON_MD = """---
name: "A Founder"
tagline: "Building small tools"
bio: "A builder with one product"
products:
  - name: "Tool A"
    url: "https://example.org/tool"
---
"""

STORY_MD = """---
title: "A Journey"
subtitle: "How a tool came to life"
person: "founder-a"
date: 2026-11-03
lessons:
  - "Talk to customers"
---

An original paragraph.

:::advice{slug="listen" category="product" person="founder-a" title="Listen"}
Ask real customers.
:::
"""


class FakeGitHub:
    def __init__(self, invalid=False):
        self.invalid = invalid

    def list_files(self, repo, ref="main"):
        self.repo = repo
        return "revision123", {
            "src/content/people/founder-a.md": "person_sha",
            "src/content/journeys/journey-a.md": "story_sha",
            "src/content/events/meeting.md": "ignored_sha",
            "public/avatars/founder-a.png": "image_sha",
            **({"src/content/journeys/broken.md": "broken_sha"} if self.invalid else {}),
        }

    def read_blob(self, repo, sha):
        return {
            "person_sha": PERSON_MD,
            "story_sha": STORY_MD,
            "broken_sha": "not valid frontmatter",
        }[sha]


class IndieMDTests(unittest.TestCase):
    def test_frontmatter_preserves_body(self):
        fields, body = parse_frontmatter(STORY_MD)
        self.assertEqual(fields["title"], "A Journey")
        self.assertTrue(body.startswith("An original paragraph."))
        self.assertIn(":::advice", body)

    def test_person_and_story_provenance(self):
        person = parse_person("founder-a", PERSON_MD)
        story = parse_story("journey-a", STORY_MD, "revision123")
        self.assertEqual(person.name, "A Founder")
        self.assertEqual(person.metadata["products"][0]["name"], "Tool A")
        self.assertEqual(story.published_at, date(2026, 11, 3))
        self.assertEqual(story.person_ids, ("founder-a",))
        self.assertEqual(story.metadata["date"], "2026-11-03")
        self.assertEqual(story.metadata["source_revision"], "revision123")
        self.assertEqual(story.raw_markdown, STORY_MD)
        self.assertEqual(story.source_url, "https://indie.md/journeys/journey-a/")

    def test_adapter_collects_only_people_and_journeys(self):
        result = IndieMDAdapter(FakeGitHub()).collect()
        self.assertEqual(len(result.people), 1)
        self.assertEqual(len(result.stories), 1)
        self.assertFalse(result.errors)

    def test_one_bad_document_does_not_hide_good_ones(self):
        result = IndieMDAdapter(FakeGitHub(invalid=True)).collect()
        self.assertEqual(len(result.stories), 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("broken.md", result.errors[0])

    def test_missing_fields_fail_validation(self):
        with self.assertRaises(ContentError):
            parse_story("bad", "---\ntitle: Nope\n---\nBody", "r")
        with self.assertRaises(ContentError):
            parse_frontmatter("No YAML header")


if __name__ == "__main__":
    unittest.main()

