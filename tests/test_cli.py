"""Collector ordering protects fresh Substack coverage during indie.md backfill."""

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from overseas_stories import cli
from overseas_stories.models import Collection


class CliSourceOrderTests(unittest.TestCase):
    def test_all_sources_sync_rss_first_and_indie_last_with_one_budget(self):
        seen = []

        def make_collection(key, kind):
            return Collection((), (), "revision", source_key=key, source_name=key,
                              source_url="https://example.org/", source_kind=kind)

        def fake_run(collection, dry_run, extractor, budget):
            seen.append((collection.source_key, id(budget)))
            return {"source_key": collection.source_key, "errors": []}

        rss = lambda source: make_collection(source.key, "substack_rss")
        indie = lambda: make_collection("indie.md", "github_markdown")
        with patch.dict(os.environ, {"SUBSTACK_CONTENT_AUTHORIZED": "true"}, clear=False), \
             patch.object(cli, "_collect_substack", side_effect=rss), \
             patch.object(cli, "_collect_indie_md", side_effect=indie), \
             patch.object(cli, "_run_collection", side_effect=fake_run), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["sync", "--dry-run", "--source", "all"]), 0)
        self.assertEqual([key for key, _ in seen],
                         [source.key for source in cli.SUBSTACK_SOURCES] + ["indie.md"])
        self.assertEqual(len({budget_id for _, budget_id in seen}), 1)
        self.assertEqual(json.loads(output.getvalue())["ai_limit"], 5)

    def test_substack_is_skipped_without_explicit_authorization(self):
        with patch.dict(os.environ, {"SUBSTACK_CONTENT_AUTHORIZED": "false"}, clear=False), \
             patch.object(cli, "_collect_substack") as collect, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["sync", "--dry-run", "--source", "substack"]), 0)
        collect.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["skipped_sources"], [
            "substack:disabled_without_explicit_authorization"
        ])


if __name__ == "__main__":
    unittest.main()
