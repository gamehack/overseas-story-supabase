"""Offline unit tests for Substack RSS ingestion (no network, no credentials)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from overseas_stories.substack_rss import (
    FeedError,
    SubstackRSSAdapter,
    SubstackSource,
    SUBSTACK_SOURCES,
    canonical_url,
    html_to_markdown,
    parse_feed,
    relevance_prefilter,
)

ITEM = """<item>
  <title>Founder Story: How I Built X</title>
  <link>https://pub.substack.com/p/how-i-built-x</link>
  <guid isPermaLink="false">urn:uuid:11111111-1111-1111-1111-111111111111</guid>
  <pubDate>Mon, 02 Nov 2026 12:00:00 GMT</pubDate>
  <dc:creator>Newsletter Author</dc:creator>
  <content:encoded><![CDATA[<p>I quit my job and started a company.</p><p>We launched in 2024.</p>]]></content:encoded>
  <description>A short description.</description>
  <category>startup</category>
  <category>founder</category>
</item>"""

RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Test Publication</title>
    {items}
  </channel>
</rss>"""


def make_rss(items):
    return RSS_TEMPLATE.format(items="\n".join(items)).encode("utf-8")


class SubstackRSSParsingTests(unittest.TestCase):
    def test_parse_feed_produces_collection_with_source_fields(self):
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss([ITEM]))
        self.assertEqual(collection.source_key, "substack:demo")
        self.assertEqual(collection.source_name, "Test Publication")
        self.assertEqual(collection.source_url, "https://demo.substack.com/feed")
        self.assertEqual(collection.source_kind, "substack_rss")
        self.assertEqual(len(collection.stories), 1)
        self.assertEqual(len(collection.people), 1)
        self.assertFalse(collection.errors)

    def test_guid_used_as_source_item_id(self):
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss([ITEM]))
        story = collection.stories[0]
        self.assertEqual(story.source_item_id, "urn:uuid:11111111-1111-1111-1111-111111111111")
        self.assertEqual(story.source_url, "https://pub.substack.com/p/how-i-built-x")

    def test_falls_back_to_link_when_no_guid(self):
        item_no_guid = ITEM.replace("<guid isPermaLink=\"false\">urn:uuid:11111111-1111-1111-1111-111111111111</guid>\n", "")
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss([item_no_guid]))
        self.assertEqual(collection.stories[0].source_item_id, "https://pub.substack.com/p/how-i-built-x")

    def test_author_is_role_author_not_founder(self):
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss([ITEM]))
        story = collection.stories[0]
        self.assertEqual(len(collection.people), 1)
        person = collection.people[0]
        self.assertEqual(person.metadata["identity_basis"], "rss_dc_creator")
        self.assertEqual(story.person_roles.get(person.source_person_id), "author")
        # No founder link inferred from RSS alone.
        self.assertNotIn("founder", story.person_roles.values())

    def test_content_hash_sha256(self):
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss([ITEM]))
        story = collection.stories[0]
        self.assertTrue(story.content_hash)
        self.assertEqual(len(story.content_hash), 64)

    def test_source_provenance_metadata(self):
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss([ITEM]))
        attribution = collection.stories[0].metadata["source_attribution"]
        self.assertEqual(attribution["publication"], "Test Publication")
        self.assertEqual(attribution["author"], "Newsletter Author")
        self.assertEqual(attribution["requested_feed_url"], "https://demo.substack.com/feed")
        self.assertEqual(attribution["canonical_permalink"], "https://pub.substack.com/p/how-i-built-x")
        self.assertEqual(attribution["guid"], "urn:uuid:11111111-1111-1111-1111-111111111111")
        self.assertFalse(collection.stories[0].metadata["content_access"]["paywall_bypassed"])

    def test_excerpt_only_detection(self):
        short_item = """<item>
          <title>Quick note</title>
          <link>https://pub.substack.com/p/short</link>
          <pubDate>Mon, 02 Nov 2026 12:00:00 GMT</pubDate>
          <content:encoded><![CDATA[<p>Continue reading</p>]]></content:encoded>
          <description>d</description>
        </item>"""
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss([short_item]))
        access = collection.stories[0].metadata["content_access"]
        self.assertTrue(access["excerpt_only"])
        self.assertIsNotNone(access["reason"])

    def test_relevance_prefilter(self):
        self.assertTrue(relevance_prefilter("Founder Story: How I Built X", "I built a company")[0])
        self.assertFalse(relevance_prefilter("Ten productivity tips", "Here is a checklist for you")[0])

    def test_audited_founder_stories_no_longer_missed(self):
        # Examples of previously rejected RSS items from the 20-item M5 audit.
        audited = (
            ("#4 on Product Hunt, 1,000 Users in a Week, and Their First Paying Customer",
             "That's the story of two brothers who founded Buildpad."),
            ("He Helped Take a SaaS Company to $2M ARR — Then Walked Away to Build Serplock",
             "We sat down with Zeeshan who built Serplock."),
            ("The AI bill nobody talks about. Meet the Austin founder fixing it.",
             "I wanted you to meet the founder behind it."),
            ("The surprising reason people are more candid with AI than colleagues",
             "Today's featured interview is with a founder trio."),
            ("Michael Rode Turned His Gym Progress Photos Into an AI Coach",
             "#### Q. What inspired you to build it?"),
            ("Most Hiring Platforms Work for Companies. Jobric Was Built for Everyone Else.",
             "In this interview, Erik shares how he got started."),
            ("The 16 Year Old Founder Building an AI Assistant for Everything on Your Screen",
             "In this interview, Mihai explains his journey."),
            ("The Pinterest Extension Quietly Exploding to Thousands of Installs with One Simple Insight",
             "#### Q. What did you learn?"),
            ("He Helped Companies Make People Spend More. Now He’s Building the Opposite.",
             "His backstory and how he started is fascinating."),
            ("Your Funnel Might Be Losing Conversions Because It’s Too Generic",
             "In this interview, Hugues shares his startup story."),
            ("I'm Back.", "My a16z-backed startup fell apart. I am building again."),
            ("What Keeps Me Building When It's This Hard",
             "My co-founder and I introduced SproutAI after two years of work."),
        )
        for title, body in audited:
            with self.subTest(title=title):
                eligible, reasons = relevance_prefilter(title, body)
                self.assertTrue(eligible, (title, reasons))

    def test_interview_questions_allow_markdown_headings_but_not_generic_advice(self):
        for prefix in ("Q. ", "Q: ", "#### Q. ", "### Q: "):
            with self.subTest(prefix=prefix):
                allowed, reasons = relevance_prefilter("Jane launched Example", "\n" + prefix + "Why did you found it?")
                self.assertTrue(allowed)
                self.assertIn("interview_or_origin_language", reasons)
        excluded = (
            ("Building in Public: A Practical Guide for Founders and Small Teams", "Here are five steps for all founders."),
            ("Sunday Picks #01: Five Indie Products Earning Trust in Public", "A roundup of five products."),
            ("Five founder habits", "Tips for growing your startup and succeeding."),
            ("Your growth checklist", "A generic interview question: how to get more leads."),
            ("How to build your startup", "In this interview we discuss the top seven generic tips."),
            ("Where We've Been (And What We Built)", "A note from our newsletter team."),
        )
        for title, body in excluded:
            with self.subTest(title=title):
                self.assertFalse(relevance_prefilter(title, body)[0])

    def test_html_to_markdown(self):
        md = html_to_markdown("<p>Hello <strong>world</strong>.</p><a href=\"https://example.org\">link</a>")
        self.assertIn("Hello **world**.", md)
        self.assertIn("[link](https://example.org)", md)

    def test_invalid_xml_raises(self):
        with self.assertRaises(FeedError):
            parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), b"not xml")

    def test_empty_channel_raises(self):
        with self.assertRaises(FeedError):
            parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), b"<rss version=\"2.0\"><channel></channel></rss>")

    def test_partial_failure_isolates_bad_item(self):
        bad_item = """<item>
          <link>https://pub.substack.com/p/bad</link>
          <pubDate>Mon, 02 Nov 2026 12:00:00 GMT</pubDate>
          <content:encoded><![CDATA[<p>bad</p>]]></content:encoded>
        </item>"""
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss([ITEM, bad_item, ITEM]))
        self.assertEqual(len(collection.stories), 2)
        self.assertEqual(len(collection.errors), 1)
        self.assertIn("item 2", collection.errors[0])

    def test_twenty_item_window_warning(self):
        items = []
        for i in range(20):
            items.append(ITEM.replace("11111111-1111-1111-1111-111111111111", f"11111111-1111-1111-1111-{i:012d}"))
        collection = parse_feed(SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed"), make_rss(items))
        self.assertTrue(any("20 items" in w for w in collection.warnings))
        self.assertEqual(collection.monitoring["feed_item_count"], 20)

    def test_canonical_url_strips_utm(self):
        self.assertEqual(canonical_url("https://pub.substack.com/p/x?utm_source=email"), "https://pub.substack.com/p/x")
        self.assertEqual(canonical_url("https://pub.substack.com/p/x?foo=1&utm_medium=web"), "https://pub.substack.com/p/x?foo=1")


class SubstackAdapterFaultIsolationTests(unittest.TestCase):
    def test_adapter_requires_explicit_authorization(self):
        source = SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed")
        with patch("overseas_stories.substack_rss.fetch_feed") as fetch:
            collection = SubstackRSSAdapter(source).collect()
        fetch.assert_not_called()
        self.assertTrue(collection.errors)
        self.assertIn("explicit authorization", collection.errors[0])

    def test_adapter_feed_error_is_captured(self):
        source = SubstackSource("substack:demo", "Demo", "https://demo.substack.com/feed")
        with patch("overseas_stories.substack_rss.fetch_feed", side_effect=FeedError("feed request failed")):
            collection = SubstackRSSAdapter(source, content_authorized=True).collect()
        self.assertEqual(len(collection.stories), 0)
        self.assertEqual(len(collection.errors), 1)
        self.assertEqual(collection.source_kind, "substack_rss")


class FiveCuratedSourcesTests(unittest.TestCase):
    def test_all_five_sources_configured(self):
        keys = [s.key for s in SUBSTACK_SOURCES]
        expected = [
            "substack:indieniche",
            "substack:behindthelaunchh",
            "substack:buildinpublichub",
            "substack:unstuckentrepreneur",
            "substack:thefoundingjourney",
        ]
        self.assertEqual(keys, expected)
        for source in SUBSTACK_SOURCES:
            self.assertTrue(source.feed_url.startswith("https://") and source.feed_url.endswith("/feed"))

    def test_each_curated_source_parses_its_fixture(self):
        for index, source in enumerate(SUBSTACK_SOURCES):
            with self.subTest(source=source.key):
                fixture = ITEM.replace(
                    "11111111-1111-1111-1111-111111111111",
                    f"11111111-1111-1111-1111-{index:012d}",
                ).replace("Newsletter Author", f"Author {index}")
                collection = parse_feed(source, make_rss([fixture]), source.feed_url)
                self.assertEqual(collection.source_key, source.key)
                self.assertEqual(collection.source_kind, "substack_rss")
                self.assertEqual(len(collection.stories), 1)
                self.assertEqual(len(collection.people), 1)


if __name__ == "__main__":
    unittest.main()
