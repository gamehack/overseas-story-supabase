import json
import os
import unittest
from datetime import date
from unittest.mock import patch

from overseas_stories.extraction import ExtractionError, StructuredExtractor
from overseas_stories.models import Story


def make_story(body="Alice founded Acme. I made $500 in my first month."):
    return Story(
        source_item_id="item-1",
        title="How Alice built Acme",
        subtitle="",
        source_url="https://example.substack.com/p/acme",
        published_at=date(2026, 11, 2),
        raw_markdown=body,
        body_markdown=body,
        person_ids=(),
        person_roles={},
        metadata={},
        content_hash="hash",
    )


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, _limit):
        return json.dumps(self.value).encode()


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "AI_EXTRACTION_ENDPOINT": "https://gateway.example/v1/chat/completions",
            "AI_EXTRACTION_API_KEY": "test-key",
            "AI_EXTRACTION_MODEL": "minimax-cn/MiniMax-M3",
        }

    def test_request_payload_matches_minimax_pattern_and_marks_article_untrusted(self):
        with patch.dict(os.environ, self.env, clear=False):
            extractor = StructuredExtractor()
        payload = extractor._build_payload(make_story())
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["max_completion_tokens"], 4096)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("max_tokens", payload)
        self.assertIn("untrusted data", payload["messages"][0]["content"])
        self.assertIn("at most 3", payload["messages"][0]["content"])
        self.assertIn("at most 240 characters", payload["messages"][0]["content"])

    def test_extract_parses_text_parts_and_persists_usage_and_versions(self):
        model_content = {
            "relevant": True,
            "subject_founders": [{"name": "Alice", "evidence": "Alice founded Acme."}],
            "milestones": [{
                "value": "founded Acme",
                "evidence": "Alice founded Acme.",
                "self_stated": False,
            }],
            "founder_numbers": [{
                "value": "$500",
                "evidence": "I made $500 in my first month.",
                "self_stated": True,
            }],
        }
        response = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": [
                    {"type": "text", "text": json.dumps(model_content)[:20]},
                    {"type": "text", "text": json.dumps(model_content)[20:]},
                ]},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        }
        with patch.dict(os.environ, self.env, clear=False):
            extractor = StructuredExtractor()
        with patch("overseas_stories.extraction.urllib.request.urlopen", return_value=FakeResponse(response)) as call:
            result = extractor.extract(make_story(), "substack:demo")
        request = call.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(sent["model"], "minimax-cn/MiniMax-M3")
        self.assertEqual(call.call_args.kwargs["timeout"], 90)
        self.assertEqual(result.data["finish_reason"], "stop")
        self.assertEqual(result.data["token_usage"], {
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "total_tokens": 200,
        })
        self.assertEqual(result.data["schema_version"], 2)
        self.assertEqual(result.data["extractor_version"], "2")
        self.assertTrue(result.data["evidence_check_passed"])
        self.assertTrue(result.data["extracted_at"].endswith("+00:00"))
        self.assertEqual([person.name for person in result.founders], ["Alice"])

    def test_string_content_parses(self):
        parsed, finish_reason, usage = StructuredExtractor._parse_envelope({
            "choices": [{"finish_reason": "stop", "message": {"content": '{"relevant":false}'}}],
            "usage": {"prompt_tokens": 1},
        })
        self.assertEqual(parsed, {"relevant": False})
        self.assertEqual(finish_reason, "stop")
        self.assertEqual(usage["prompt_tokens"], 1)

    def test_exact_evidence_and_self_stated_number_are_required(self):
        validated = StructuredExtractor._validate({
            "relevant": True,
            "subject_founders": [
                {"name": "Alice", "evidence": "Alice founded Acme."},
                {"name": "Mallory", "evidence": "alice founded acme."},
            ],
            "milestones": [
                {"value": "valid", "evidence": "Alice founded Acme."},
                {"value": "wrong case", "evidence": "alice founded acme."},
            ],
            "founder_numbers": [
                {"value": "$500", "evidence": "I made $500 in my first month.", "self_stated": True},
                {"value": "$500", "evidence": "I made $500 in my first month.", "self_stated": False},
                {"value": "$900", "evidence": "I made $500 in my first month.", "self_stated": True},
            ],
        }, make_story().body_markdown)
        self.assertEqual([item["name"] for item in validated["subject_founders"]], ["Alice"])
        self.assertEqual(len(validated["milestones"]), 1)
        self.assertEqual(len(validated["founder_numbers"]), 1)

    def test_validation_caps_founders_and_facts_to_fit_output_budget(self):
        body = " ".join([
            "Founder One built A.",
            "Founder Two built B.",
            "Founder Three built C.",
            "Founder Four built D.",
            "Milestone one.",
            "Milestone two.",
            "Milestone three.",
            "Milestone four.",
        ])
        value = {
            "relevant": True,
            "subject_founders": [
                {"name": f"Founder {name}", "evidence": f"Founder {name} built {product}."}
                for name, product in (("One", "A"), ("Two", "B"), ("Three", "C"), ("Four", "D"))
            ],
            "milestones": [
                {"value": f"milestone {name}", "evidence": f"Milestone {name}."}
                for name in ("one", "two", "three", "four")
            ],
        }
        validated = StructuredExtractor._validate(value, body)
        self.assertEqual(len(validated["subject_founders"]), 3)
        self.assertEqual(len(validated["milestones"]), 3)

    def test_length_response_is_safe_and_keeps_usage(self):
        with self.assertRaises(ExtractionError) as ctx:
            StructuredExtractor._parse_envelope({
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": "private raw model response"},
                }],
                "usage": {"completion_tokens": 2048, "total_tokens": 2200},
            })
        self.assertEqual(ctx.exception.finish_reason, "length")
        self.assertEqual(ctx.exception.token_usage["completion_tokens"], 2048)
        self.assertNotIn("private raw model response", str(ctx.exception))

    def test_ai_endpoint_requires_https(self):
        StructuredExtractor._validate_endpoint("https://gateway.example/v1/chat/completions")
        for endpoint in (
            "http://omniroute:20128/v1/chat/completions",
            "http://example.com/v1/chat/completions",
            "https://user:password@gateway.example/v1/chat/completions",
        ):
            with self.assertRaises(ValueError):
                StructuredExtractor._validate_endpoint(endpoint)


if __name__ == "__main__":
    unittest.main()
