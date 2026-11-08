"""M3 unit tests: no database, network, or production writes."""

import unittest
from unittest.mock import patch

from overseas_stories.editorial import (
    extract_facts, main, make_case, score_story,
)


BODY = "I started the project because I needed it. Our MVP failed initially. We learned to talk to users. Revenue is $500 monthly."


def ai():
    return {
        "status": "complete", "relevant": True, "evidence_check_passed": True,
        "origins": [{"value": "started because I needed it", "evidence": "I started the project because I needed it."}],
        "setbacks": [{"value": "MVP failed", "evidence": "Our MVP failed initially."}],
        "lessons": [{"value": "talk to users", "evidence": "We learned to talk to users."}],
        "founder_numbers": [{"value": "$500 monthly", "evidence": "Revenue is $500 monthly.", "self_stated": True}],
        "startup_products": [{"value": "My Test App", "evidence": "I started the project because I needed it."}],
    }


class EditorialTests(unittest.TestCase):
    def test_evidence_must_be_exact_original_substring(self):
        content = ai()
        content["lessons"].append({"value": "hallucinated advice", "evidence": "Not in the source."})
        facts = extract_facts(content, BODY)
        self.assertEqual(len(facts), 5)
        self.assertFalse(any(f.value == "hallucinated advice" for f in facts))

    def test_stable_dedupe_and_self_statement(self):
        content = ai()
        content["founder_numbers"].append(content["founder_numbers"][0])
        facts = extract_facts(content, BODY)
        self.assertEqual(sum(f.kind == "founder_numbers" for f in facts), 1)
        self.assertTrue(next(f.self_stated for f in facts if f.kind == "founder_numbers"))

    def test_score_is_deterministic_and_bounded(self):
        facts = extract_facts(ai(), BODY)
        result = score_story(ai(), facts, 1, "https://example.com/source")
        self.assertEqual(result["total"], 72)
        self.assertLessEqual(result["total"], 100)
        self.assertEqual(score_story({}, facts, 1, "https://example.com")["total"], 0)

    def test_no_inferred_cross_source_identity(self):
        content = ai()
        story = {"id": "11111111-1111-1111-1111-111111111111", "title": "Founder story",
                 "source_url": "https://example.com/source", "body_markdown": BODY,
                 "metadata": {"ai_extraction": content}, "founder_count": 1}
        case = make_case(story)
        self.assertEqual(len(case["projects"]), 1)
        self.assertNotIn("person_name", case)
        self.assertEqual(case["story_id"], story["id"])

    def test_legacy_channel_flags_are_rejected(self):
        for argv in (
            ["--wechat-test-draft", "11111111-1111-1111-1111-111111111111"],
            ["--confirm-wechat-test"],
        ):
            with self.subTest(argv=argv), patch("overseas_stories.editorial._db") as db:
                with self.assertRaises(SystemExit) as exit_status:
                    main(argv)
                self.assertEqual(exit_status.exception.code, 2)
                db.assert_not_called()

    def test_main_retains_preview_apply_and_manual_review(self):
        story_id = "11111111-1111-1111-1111-111111111111"
        with patch("overseas_stories.editorial.index_stories", return_value={"count": 0, "items": []}) as index:
            self.assertEqual(main([]), 0)
            self.assertEqual(main(["--apply", "--story-id", story_id]), 0)
            self.assertEqual(index.call_args_list[0].args[1:], (False, None))
            self.assertEqual(index.call_args_list[1].args[1:], (True, story_id))
        with patch("overseas_stories.editorial.request_review", return_value={"status": "review_requested"}) as review:
            self.assertEqual(main(["--request-review", story_id]), 0)
            self.assertEqual(review.call_args.args[1], story_id)
        with self.assertRaises(SystemExit):
            main(["--request-review", story_id, "--apply"])


if __name__ == "__main__":
    unittest.main()
