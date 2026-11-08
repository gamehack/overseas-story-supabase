"""M4 tests: fully offline, no database, gateway or publishing actions."""

import json
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from overseas_stories.localize import (
    INDEXED_PROMPT_VERSION, PROMPT_VERSION, ChineseLocalizer,
    _parse_localization_envelope, _validate_indexed_localization,
    _validate_localization, fact_source_hash, localize_cases, main,
    story_source_hash,
)


FACT = {
    "id": "e6248e10-2cba-411b-8a34-de3afcdd49ac",
    "fact_type": "origins", "value": "The founder began after a failed launch",
    "evidence": "The founder began after a failed launch", "self_stated": False,
}
STORY = {
    "id": "41f5dbda-7eab-4e56-8956-8e5cb3796ee4",
    "title": "Founding story", "source_url": "https://example.org/story",
    "content_hash": "sourcehash", "saved_hash": None, "facts": [FACT],
}
FACTS_14 = [
    dict(
        FACT,
        id=f"00000000-0000-4000-8000-{index:012d}",
        value=f"Source fact number {index}",
        evidence=f"Exact source evidence number {index}",
    )
    for index in range(1, 15)
]


def good_response():
    sentence = "创业者介绍了项目的起点，以及经历挫折后的调整，相关经历均来自公开叙述。"
    synopsis = (sentence * 12)[:390]
    assert 300 <= len(synopsis) <= 500
    return {
        "title_zh": "创始人的创业转折",
        "synopsis_zh": synopsis,
        "outline_zh": {
            "起点": "创业者经历一次失败后开始探索。", "动机": "资料未说明。",
            "难题": "经历失败。", "转折": "资料未说明。",
            "结果与启示": "应依据原始证据理解经历。",
        },
        "facts": [{"id": FACT["id"], "value_zh": "创始人在一次失败的发布后开始创业。"}],
    }


def indexed_response(facts=FACTS_14):
    response = good_response()
    response["facts"] = [
        {"index": index, "value_zh": f"这是与第{index}条英文证据对应的中文事实。"}
        for index, _ in enumerate(facts, start=1)
    ]
    return response


class FakeLocalizer:
    configured = True
    model = "test-model"

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def localize(self, story):
        self.calls += 1
        if self.fail:
            raise ValueError("AI response reached the output token limit")
        return _validate_localization(good_response(), story["facts"]), {"total_tokens": 30}


class LocalizerTests(unittest.TestCase):
    def test_validates_one_translation_per_fact(self):
        result = _validate_localization(good_response(), [FACT])
        self.assertEqual(result["facts"][FACT["id"]], "创始人在一次失败的发布后开始创业。")

    def test_rejects_model_invented_fact_id(self):
        output = good_response()
        output["facts"][0]["id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown or duplicated"):
            _validate_localization(output, [FACT])

    def test_rejects_missing_or_duplicate_fact(self):
        output = good_response()
        output["facts"] = []
        with self.assertRaisesRegex(ValueError, "one translation"):
            _validate_localization(output, [FACT])

    def test_indexed_protocol_maps_all_fourteen_positions_to_source_ids(self):
        output = indexed_response()
        output["facts"].reverse()
        result = _validate_indexed_localization(output, FACTS_14)
        self.assertEqual(len(result["facts"]), 14)
        for index, fact in enumerate(FACTS_14, start=1):
            self.assertEqual(
                result["facts"][fact["id"]],
                f"这是与第{index}条英文证据对应的中文事实。",
            )

    def test_indexed_protocol_rejects_missing_duplicate_and_bad_indices(self):
        invalid = {}
        missing = indexed_response()
        missing["facts"].pop()
        invalid["missing"] = missing
        duplicate = indexed_response()
        duplicate["facts"][-1]["index"] = 1
        invalid["duplicate"] = duplicate
        zero = indexed_response()
        zero["facts"][0]["index"] = 0
        invalid["zero"] = zero
        too_large = indexed_response()
        too_large["facts"][-1]["index"] = 15
        invalid["too_large"] = too_large
        string_index = indexed_response()
        string_index["facts"][0]["index"] = "1"
        invalid["string"] = string_index
        bool_index = indexed_response()
        bool_index["facts"][0]["index"] = True
        invalid["bool"] = bool_index
        for name, output in invalid.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                _validate_indexed_localization(output, FACTS_14)

    def test_indexed_protocol_rejects_non_chinese_value(self):
        output = indexed_response()
        output["facts"][0]["value_zh"] = "English only"
        with self.assertRaisesRegex(ValueError, "readable in Chinese"):
            _validate_indexed_localization(output, FACTS_14)

    def test_indexed_protocol_rejects_ambiguous_extra_fields(self):
        output = indexed_response()
        output["facts"][0]["id"] = FACTS_14[0]["id"]
        with self.assertRaisesRegex(ValueError, "only index"):
            _validate_indexed_localization(output, FACTS_14)

    def test_rejects_short_summary(self):
        output = good_response()
        output["synopsis_zh"] = "太短"
        with self.assertRaisesRegex(ValueError, "300"):
            _validate_localization(output, [FACT])

    def test_slightly_short_summary_uses_existing_fact_only(self):
        output = good_response()
        output["synopsis_zh"] = output["synopsis_zh"][:285]
        result = _validate_localization(output, [FACT])
        self.assertGreaterEqual(len(result["synopsis_zh"]), 300)
        self.assertIn("创始人在一次失败的发布后开始创业", result["synopsis_zh"])

    def test_long_summary_ends_on_sentence_boundary(self):
        output = good_response()
        output["synopsis_zh"] = ("创业者如实记录了产品起点与困难。" * 42)
        result = _validate_localization(output, [FACT])
        self.assertTrue(300 <= len(result["synopsis_zh"]) <= 500)
        self.assertTrue(result["synopsis_zh"].endswith("。"))

    def test_recovers_trailing_comma_and_line_breaks_in_model_json(self):
        broken = '{"title_zh": "第一行\n第二行", "facts": [],}'
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": broken}}],
                    "usage": {"total_tokens": 42}}
        result, usage = _parse_localization_envelope(envelope)
        self.assertEqual(result["title_zh"], "第一行\n第二行")
        self.assertEqual(usage["total_tokens"], 42)

    def test_rejects_unrecoverable_json(self):
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": '{"facts": [oops]}'}}]}
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            _parse_localization_envelope(envelope)

    def test_json_diagnostic_is_structural_only(self):
        content = '{"secret_source": "confidential", "facts": [oops]}'
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": content}}],
                    "usage": {"completion_tokens": 301}}
        with self.assertRaises(ValueError) as error:
            _parse_localization_envelope(envelope)
        self.assertIn("finish=stop", str(error.exception))
        self.assertIn("output_tokens=301", str(error.exception))
        self.assertNotIn("confidential", str(error.exception))
        self.assertNotIn("secret_source", str(error.exception))

    def test_repairs_missing_commas_between_complete_json_values(self):
        malformed = ('{"title_zh":"中文标题" "synopsis_zh":"简介",'
                     '"facts":[{"id":"one","value_zh":"第一条"} '
                     '{"id":"two","value_zh":"第二条"}]}')
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": malformed}}]}
        result, _ = _parse_localization_envelope(envelope)
        self.assertEqual(result["title_zh"], "中文标题")
        self.assertEqual(len(result["facts"]), 2)

    def test_does_not_guess_missing_delimiter_inside_prose(self):
        malformed = '{"title_zh": "所谓的"最好"产品", "facts": []}'
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": malformed}}]}
        result, _ = _parse_localization_envelope(envelope)
        self.assertEqual(result["title_zh"], '所谓的"最好"产品')

    def test_embedded_ascii_quotes_in_chinese_fact_are_preserved(self):
        malformed = ('{"title_zh":"中文", "facts":[{"id":"one",'
                     '"value_zh":"他称工具是"最好"的选择。"}]}')
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": malformed}}]}
        result, _ = _parse_localization_envelope(envelope)
        self.assertEqual(result["facts"][0]["value_zh"], '他称工具是"最好"的选择。')

    def test_repair_library_fallback_handles_nonstandard_separator(self):
        broken = ('{"title_zh":"创业故事";"synopsis_zh":"创业者记录了自己项目的经历。",'
                  '"facts":[{"id":"one","value_zh":"有一条对应中文事实。"}]}')
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": broken}}],
                    "usage": {"total_tokens": 58}}
        result, usage = _parse_localization_envelope(envelope)
        self.assertEqual(result["title_zh"], "创业故事")
        self.assertEqual(result["facts"][0]["id"], "one")
        self.assertEqual(usage["total_tokens"], 58)

    def test_repair_never_bypasses_exact_fact_ids(self):
        broken = ('{"title_zh":"创业故事";"synopsis_zh":"创业",'
                  '"facts":[{"id":"wrong","value_zh":"任意中文"}]}')
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": broken}}]}
        repaired, _ = _parse_localization_envelope(envelope)
        with self.assertRaisesRegex(ValueError, "unknown or duplicated"):
            _validate_localization(repaired, [FACT])

    def test_recovers_first_complete_object_before_extra_json(self):
        valid = good_response()
        trailing = json.dumps({"note": "second object is not the translation"})
        content = json.dumps(valid, ensure_ascii=False) + "\n" + trailing
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": content}}],
                    "usage": {"completion_tokens": 1283}}
        parsed, usage = _parse_localization_envelope(envelope)
        self.assertEqual(parsed, valid)
        self.assertEqual(usage["completion_tokens"], 1283)
        self.assertEqual(_validate_localization(parsed, [FACT])["facts"][FACT["id"]],
                         valid["facts"][0]["value_zh"])

    def test_extra_data_recovery_never_bypasses_fact_validation(self):
        incomplete = good_response()
        incomplete["facts"][0]["id"] = "unknown"
        content = json.dumps(incomplete, ensure_ascii=False) + "\n{}"
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        parsed, _ = _parse_localization_envelope(envelope)
        with self.assertRaisesRegex(ValueError, "unknown or duplicated"):
            _validate_localization(parsed, [FACT])

    def test_rejects_non_chinese_title(self):
        output = good_response()
        output["title_zh"] = "English only"
        with self.assertRaisesRegex(ValueError, "title"):
            _validate_localization(output, [FACT])

    def test_missing_outline_falls_back_to_source_linked_translations(self):
        output = good_response()
        output.pop("outline_zh")
        result = _validate_localization(output, [FACT])
        self.assertEqual(result["outline_zh"]["起点"], "创始人在一次失败的发布后开始创业。")
        self.assertEqual(result["outline_zh"]["动机"], "")
        self.assertEqual(len(result["outline_zh"]), 5)

    def test_model_five_stage_list_is_accepted(self):
        output = good_response()
        output["outline_zh"] = ["起点内容", "动机内容", "困难内容", "转折内容", "启示内容"]
        result = _validate_localization(output, [FACT])
        self.assertEqual(result["outline_zh"]["转折"], "转折内容")

    def test_source_hash_is_stable_and_changes_with_evidence(self):
        first = story_source_hash(STORY, "model")
        self.assertEqual(
            first,
            "200bddc0563384f59ac79e6ec6be7ac177c1df1529d5ba22ed7e0aedf0b6cc98",
        )
        self.assertEqual(first, story_source_hash(STORY, "model"))
        indexed = story_source_hash(STORY, "model", INDEXED_PROMPT_VERSION)
        self.assertNotEqual(first, indexed)
        self.assertEqual(first, story_source_hash(STORY, "model", PROMPT_VERSION))
        changed = dict(STORY, facts=[dict(FACT, evidence="different evidence")])
        self.assertNotEqual(first, story_source_hash(changed, "model"))
        self.assertNotEqual(fact_source_hash(FACT), fact_source_hash(changed["facts"][0]))

    def test_preview_does_not_call_or_write(self):
        loc = FakeLocalizer()
        with patch("overseas_stories.localize._db", return_value=nullcontext(object())), \
             patch("overseas_stories.localize._load_cases", return_value=[STORY]), \
             patch("overseas_stories.localize._save_localization") as save:
            result = localize_cases("fake", loc, apply=False)
        self.assertEqual(loc.calls, 0)
        self.assertEqual(result["items"][0]["status"], "pending")
        save.assert_not_called()

    def test_idempotent_current_case_is_skipped(self):
        loc = FakeLocalizer()
        current = dict(STORY, saved_hash=story_source_hash(STORY, loc.model))
        with patch("overseas_stories.localize._db", return_value=nullcontext(object())), \
             patch("overseas_stories.localize._load_cases", return_value=[current]), \
             patch("overseas_stories.localize._save_localization") as save:
            result = localize_cases("fake", loc, apply=True)
        self.assertEqual(result["skipped_current"], 1)
        self.assertEqual(result["calls_used"], 0)
        save.assert_not_called()

    def test_indexed_hash_is_current_in_default_preview(self):
        loc = FakeLocalizer()
        current = dict(
            STORY,
            saved_hash=story_source_hash(STORY, loc.model, INDEXED_PROMPT_VERSION),
        )
        with patch("overseas_stories.localize._db", return_value=nullcontext(object())), \
             patch("overseas_stories.localize._load_cases", return_value=[current]), \
             patch("overseas_stories.localize._save_localization") as save:
            result = localize_cases("fake", loc, apply=False)
        self.assertEqual(result["skipped_current"], 1)
        self.assertEqual(result["calls_used"], 0)
        save.assert_not_called()

    def test_indexed_hash_becomes_stale_when_source_evidence_changes(self):
        loc = FakeLocalizer()
        changed = dict(
            STORY,
            facts=[dict(FACT, evidence="Changed exact source evidence")],
            saved_hash=story_source_hash(STORY, loc.model, INDEXED_PROMPT_VERSION),
        )
        with patch("overseas_stories.localize._db", return_value=nullcontext(object())), \
             patch("overseas_stories.localize._load_cases", return_value=[changed]), \
             patch("overseas_stories.localize._save_localization") as save:
            result = localize_cases("fake", loc, apply=False)
        self.assertEqual(result["skipped_current"], 0)
        self.assertEqual(result["items"][0]["status"], "pending")
        save.assert_not_called()

    def test_success_writes_only_bilingual_rows(self):
        loc = FakeLocalizer()
        with patch("overseas_stories.localize._db", return_value=nullcontext(object())), \
             patch("overseas_stories.localize._load_cases", return_value=[STORY]), \
             patch("overseas_stories.localize._save_localization") as save:
            result = localize_cases("fake", loc, apply=True)
        self.assertEqual(result["calls_used"], 1)
        self.assertEqual(result["items"][0]["status"], "complete")
        self.assertEqual(save.call_count, 1)

    def test_each_story_uses_its_own_transaction(self):
        class Conn:
            autocommit = False
        conn = Conn()
        loc = FakeLocalizer()
        with patch("overseas_stories.localize._db", return_value=nullcontext(conn)), \
             patch("overseas_stories.localize._load_cases", return_value=[STORY]), \
             patch("overseas_stories.localize._save_localization") as save:
            localize_cases("fake", loc, apply=True)
        self.assertTrue(conn.autocommit)
        save.assert_called_once()

    def test_model_failure_never_writes(self):
        loc = FakeLocalizer(fail=True)
        with patch("overseas_stories.localize._db", return_value=nullcontext(object())), \
             patch("overseas_stories.localize._load_cases", return_value=[STORY]), \
             patch("overseas_stories.localize._save_localization") as save:
            result = localize_cases("fake", loc, apply=True)
        self.assertEqual(result["calls_used"], 1)
        self.assertEqual(result["items"][0]["status"], "error")
        save.assert_not_called()

    def test_invalid_indexed_response_never_writes(self):
        class InvalidIndexedLocalizer(FakeLocalizer):
            prompt_version = INDEXED_PROMPT_VERSION

            def localize(self, story):
                self.calls += 1
                output = indexed_response(story["facts"])
                output["facts"][-1]["index"] = 1
                return _validate_indexed_localization(output, story["facts"]), {}

        story = dict(STORY, facts=FACTS_14)
        loc = InvalidIndexedLocalizer()
        with patch("overseas_stories.localize._db", return_value=nullcontext(object())), \
             patch("overseas_stories.localize._load_cases", return_value=[story]), \
             patch("overseas_stories.localize._save_localization") as save:
            result = localize_cases("fake", loc, apply=True, limit=1)
        self.assertEqual(result["calls_used"], 1)
        self.assertEqual(result["items"][0]["status"], "error")
        save.assert_not_called()

    def test_successful_indexed_case_saves_its_protocol_version(self):
        class IndexedLocalizer(FakeLocalizer):
            prompt_version = INDEXED_PROMPT_VERSION

            def localize(self, story):
                self.calls += 1
                return _validate_indexed_localization(
                    indexed_response(story["facts"]), story["facts"]
                ), {"total_tokens": 30}

        story = dict(STORY, facts=FACTS_14)
        loc = IndexedLocalizer()
        with patch("overseas_stories.localize._db", return_value=nullcontext(object())), \
             patch("overseas_stories.localize._load_cases", return_value=[story]), \
             patch("overseas_stories.localize._save_localization") as save:
            result = localize_cases("fake", loc, apply=True, limit=1)
        self.assertEqual(result["items"][0]["status"], "complete")
        self.assertEqual(save.call_args.args[5], INDEXED_PROMPT_VERSION)

    def test_gateway_disabled_without_config(self):
        with patch.dict("os.environ", {}, clear=True):
            localizer = ChineseLocalizer()
        self.assertFalse(localizer.configured)
        with self.assertRaisesRegex(RuntimeError, "configured"):
            localizer.localize(STORY)

    def test_manual_strict_facts_prompt_names_all_source_ids(self):
        with patch.dict("os.environ", {}, clear=True):
            standard = ChineseLocalizer()._payload(STORY)["messages"][0]["content"]
            strict = ChineseLocalizer(strict_facts=True)._payload(STORY)["messages"][0]["content"]
        self.assertNotIn("输出JSON前自行校验", standard)
        self.assertIn("输出JSON前自行校验", strict)
        self.assertIn(FACT["id"], strict)
        self.assertIn("严格输出1条facts", strict)

    def test_indexed_payload_uses_positions_and_contains_no_fact_uuid(self):
        story = dict(STORY, facts=FACTS_14)
        with patch.dict("os.environ", {}, clear=True):
            localizer = ChineseLocalizer(indexed_facts=True)
            payload = localizer._payload(story)
        source = json.loads(payload["messages"][1]["content"])
        self.assertEqual([fact["index"] for fact in source["facts"]], list(range(1, 15)))
        self.assertTrue(all(set(fact) == {
            "index", "category", "original_value", "original_evidence",
            "founder_self_stated_number",
        } for fact in source["facts"]))
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertTrue(all(fact["id"] not in serialized for fact in FACTS_14))
        self.assertIn("不得输出任何UUID", payload["messages"][0]["content"])
        self.assertEqual(localizer.prompt_version, INDEXED_PROMPT_VERSION)

    def test_manual_strict_mode_requires_one_explicit_case(self):
        with self.assertRaises(SystemExit) as err:
            main(["--strict-facts", "--apply", "--limit", "5"])
        self.assertEqual(err.exception.code, 2)

    def test_manual_indexed_mode_requires_one_explicit_applied_case(self):
        invalid_argv = [
            ["--indexed-facts", "--story-id", STORY["id"], "--limit", "1"],
            ["--indexed-facts", "--apply", "--limit", "1"],
            ["--indexed-facts", "--apply", "--story-id", STORY["id"], "--limit", "2"],
            ["--indexed-facts", "--strict-facts", "--apply", "--story-id", STORY["id"],
             "--limit", "1"],
        ]
        for argv in invalid_argv:
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as error:
                main(argv)
            self.assertEqual(error.exception.code, 2)

    def test_manual_indexed_mode_passes_selected_protocol_to_runner(self):
        result = {
            "errors": [], "mode": "apply", "items": [], "calls_used": 0,
        }
        with patch("overseas_stories.localize.localize_cases", return_value=result) as run:
            code = main([
                "--indexed-facts", "--apply", "--story-id", STORY["id"],
                "--limit", "1",
            ])
        self.assertEqual(code, 0)
        localizer = run.call_args.args[1]
        self.assertTrue(localizer.indexed_facts)
        self.assertEqual(localizer.prompt_version, INDEXED_PROMPT_VERSION)


if __name__ == "__main__":
    unittest.main()
