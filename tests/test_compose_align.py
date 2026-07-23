from __future__ import annotations

import unittest

from cutpoint_lab.quality.compose_align import compose


def _segment(segment_id: str, text: str, tokens: list[str] | None = None) -> dict:
    token_texts = tokens if tokens is not None else [text]
    return {
        "id": segment_id,
        "start_ms": 0,
        "end_ms": len(token_texts) * 100,
        "text": text,
        "tokens": [
            {
                "text": token,
                "start_ms": index * 100,
                "end_ms": (index + 1) * 100,
            }
            for index, token in enumerate(token_texts)
        ],
    }


class ComposeAlignTests(unittest.TestCase):
    def test_lines_are_independent_and_punctuation_only_lines_are_ignored(self):
        result = compose(
            [_segment("s1", "第一句"), _segment("s2", "第二句")],
            "！！！\n第一句\n\n……\n第二句",
        )

        self.assertEqual(result["edl"]["order"], ["s1", "s2"])
        self.assertEqual(
            result["report"]["stats"],
            {"total": 2, "auto": 2, "ai": 0, "unmatched": 0},
        )

    def test_greedy_window_reorder_and_repeated_reference(self):
        segments = [
            _segment("s1", "第一句讲开始"),
            _segment("s2", "第二句讲方法"),
            _segment("s3", "第三句讲结果"),
        ]

        result = compose(
            segments,
            "第二句讲方法第三句讲结果\n第一句讲开始\n第一句讲开始",
        )

        self.assertEqual(result["edl"]["order"], ["s2", "s3", "s1", "s1"])
        self.assertEqual(
            [item["segment_ids"] for item in result["report"]["paragraphs"]],
            [["s2", "s3"], ["s1"], ["s1"]],
        )
        self.assertTrue(all(row["checked"] for row in result["edl"]["rows"]))

    def test_each_sentence_can_seed_a_window_before_the_best_result_is_chosen(self):
        result = compose(
            [
                _segment("decoy", "abcdefghijklmnopZZZZ"),
                _segment("s2", "abcdefghij"),
                _segment("s3", "klmnopqrst"),
            ],
            "abcdefghijklmnopqrst",
        )

        self.assertEqual(result["edl"]["order"], ["s2", "s3"])
        self.assertEqual(result["report"]["paragraphs"][0]["similarity"], 1.0)

    def test_deletion_only_subset_is_converted_to_token_cuts(self):
        segments = [
            _segment(
                "s1",
                "今天我们重点分享方法",
                ["今天", "我们", "重点", "分享", "方法"],
            ),
            _segment("s2", "不会命中"),
        ]

        result = compose(segments, "今天重点分享方法")

        self.assertEqual(result["edl"]["order"], ["s1"])
        self.assertEqual(
            result["edl"]["rows"],
            [
                {
                    "id": "s1",
                    "checked": True,
                    "text": "今天我们重点分享方法",
                    "cuts": [{"start_token": 1, "end_token": 1}],
                },
                {"id": "s2", "checked": False, "text": "不会命中"},
            ],
        )

    def test_subset_inside_one_token_falls_back_to_whole_sentence_with_note(self):
        result = compose([_segment("s1", "abcdefghij")], "abcdefghi")

        self.assertEqual(result["edl"]["order"], ["s1"])
        self.assertNotIn("cuts", result["edl"]["rows"][0])
        self.assertIn("整句保留", result["report"]["paragraphs"][0]["note"])

    def test_similarity_boundaries_and_unlocatable_auto_match(self):
        exact_boundary = compose(
            [_segment("s1", "abcdefghijklmnopqrst", list("abcdefghijklmnopqrst"))],
            "abcXefghYjklmnopqZst",
        )
        grey_boundary = compose([_segment("s1", "ac")], "ab")

        paragraph = exact_boundary["report"]["paragraphs"][0]
        self.assertEqual(paragraph["similarity"], 0.85)
        self.assertEqual(paragraph["status"], "auto")
        self.assertIn("整句保留", paragraph["note"])
        self.assertNotIn("cuts", exact_boundary["edl"]["rows"][0])
        self.assertEqual(grey_boundary["report"]["paragraphs"][0]["similarity"], 0.5)
        self.assertEqual(grey_boundary["report"]["paragraphs"][0]["status"], "unmatched")

    def test_two_character_changes_still_auto_match(self):
        result = compose(
            [_segment("s1", "abcdefghijklmnopqrst")],
            "abcXefghYjklmnopqrZt",
        )

        self.assertGreaterEqual(result["report"]["paragraphs"][0]["similarity"], 0.85)
        self.assertEqual(result["report"]["paragraphs"][0]["status"], "auto")

    def test_grey_match_accepts_only_high_confidence_known_ids_with_aliases(self):
        calls: list[tuple[str, str]] = []

        def fake_chat(system: str, user: str) -> dict:
            calls.append((system, user))
            return {
                "matches": [
                    {
                        "paragraph_index": 0,
                        "segment_ids": ["12"],
                        "confidence": 0.9,
                        "reason": "改写自该句",
                    }
                ]
            }

        result = compose(
            [_segment("sentence_0012", "ac")],
            "ab",
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "理念与协议\n{{USER_BRIEF}}",
        )

        self.assertEqual(result["edl"]["order"], ["sentence_0012"])
        self.assertEqual(result["report"]["paragraphs"][0]["status"], "ai")
        self.assertNotIn("{{USER_BRIEF}}", calls[0][0])
        self.assertIn("sentence_0012", calls[0][1])

    def test_ai_unauthorized_id_and_low_confidence_are_unmatched(self):
        cases = [
            {
                "paragraph_index": 0,
                "segment_ids": ["invented_9999"],
                "confidence": 0.99,
                "reason": "越权",
            },
            {
                "paragraph_index": 0,
                "segment_ids": ["s1"],
                "confidence": 0.84,
                "reason": "信心不足",
            },
        ]
        for match in cases:
            with self.subTest(match=match):
                result = compose(
                    [_segment("s1", "ac")],
                    "ab",
                    chat_json_fn=lambda _system, _user, item=match: {"matches": [item]},
                )
                self.assertEqual(result["edl"]["order"], [])
                self.assertEqual(
                    result["report"]["paragraphs"][0]["status"], "unmatched"
                )

    def test_below_grey_zone_is_never_sent_to_ai(self):
        def unexpected_chat(_system: str, _user: str) -> dict:
            raise AssertionError("低相似度段落不应调用 AI")

        result = compose(
            [_segment("s1", "abcd")],
            "wxyz",
            chat_json_fn=unexpected_chat,
        )

        paragraph = result["report"]["paragraphs"][0]
        self.assertEqual(paragraph["status"], "unmatched")
        self.assertEqual(paragraph["note"], "原视频中没有这段话")
        self.assertEqual(result["edl"]["order"], [])


if __name__ == "__main__":
    unittest.main()
