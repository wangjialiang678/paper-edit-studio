from __future__ import annotations

import unittest

from cutpoint_lab.planning.quotes import (
    accept_quote,
    analyze_quote_candidates,
    update_candidate_status,
)


def _segments() -> list[dict]:
    return [
        {
            "id": f"s{index}",
            "start_ms": (index - 1) * 1000,
            "end_ms": index * 1000,
            "text": f"第 {index} 句",
        }
        for index in range(1, 8)
    ]


def _content_map(*, confirmed: bool = True) -> dict:
    return {
        "status": "confirmed",
        "topics": [
            {
                "id": "t1",
                "name": "主题一",
                "summary": "摘要",
                "segment_ids": [f"s{index}" for index in range(1, 7)],
                "status": "confirmed" if confirmed else "pending",
            },
            {
                "id": "t2",
                "name": "主题二",
                "summary": "摘要二",
                "segment_ids": ["s7"],
                "status": "confirmed",
            },
        ],
    }


class QuotePlanningTests(unittest.TestCase):
    def test_analyze_uses_confirmed_topic_context_validates_and_caps_candidates(self):
        calls: list[tuple[str, str]] = []

        def chat_json(system: str, user: str) -> dict:
            calls.append((system, user))
            candidates = [
                {
                    "id": f"q{index}",
                    "topic_id": "t1",
                    "segment_id": "1" if index == 1 else f"s{index}",
                    "type": "claim",
                    "context": "上下文",
                    "reason": "理由",
                }
                for index in range(1, 7)
            ]
            candidates.append(
                {
                    "id": "bad-topic",
                    "topic_id": "t2",
                    "segment_id": "s1",
                    "type": "hook",
                    "context": "",
                    "reason": "",
                }
            )
            return {"candidates": candidates}

        result = analyze_quote_candidates(
            _content_map(),
            _segments(),
            chat_json_fn=chat_json,
            assemble_prompt_fn=lambda: "QUOTE PROTOCOL",
            topic_id="t1",
            model="mock-model",
            now_fn=lambda: "now",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "QUOTE PROTOCOL")
        self.assertIn("[s1] 第 1 句", calls[0][1])
        self.assertNotIn("[s7]", calls[0][1])
        self.assertEqual(len(result["candidates"]), 5)
        self.assertEqual(result["candidates"][0]["segment_id"], "s1")
        self.assertTrue(all(item["status"] == "pending" for item in result["candidates"]))
        self.assertEqual(result["meta"]["source"], "ai")
        self.assertTrue(result["meta"]["warnings"])

    def test_analyze_requires_at_least_one_confirmed_topic(self):
        content_map = _content_map(confirmed=False)
        content_map["topics"][1]["status"] = "pending"
        with self.assertRaisesRegex(ValueError, "confirmed"):
            analyze_quote_candidates(
                content_map,
                _segments(),
                chat_json_fn=lambda *_args: {},
                assemble_prompt_fn=lambda: "",
            )

    def test_accept_marks_quote_locked_and_promote_materializes_original_order(self):
        edl = {
            "rows": [
                {"id": "s1", "checked": True, "text": "第一句"},
                {"id": "s2", "checked": False, "text": "第二句"},
                {"id": "s3", "checked": True, "text": "第三句"},
            ],
            "order": [],
        }
        updated = accept_quote(
            edl,
            {"id": "q1", "segment_id": "s2"},
            promote=True,
        )
        rows = {row["id"]: row for row in updated["rows"]}
        self.assertTrue(rows["s2"]["checked"])
        self.assertEqual(rows["s2"]["role"], "quote")
        self.assertIs(rows["s2"]["locked"], True)
        self.assertEqual(updated["order"], ["s2", "s1", "s3"])
        self.assertFalse("role" in edl["rows"][1])

    def test_accept_without_promote_keeps_existing_order_but_makes_quote_effective(self):
        edl = {
            "rows": [
                {"id": "s1", "checked": True},
                {"id": "s2", "checked": False},
                {"id": "s3", "checked": True},
            ],
            "order": ["s3", "s1"],
        }
        updated = accept_quote(
            edl,
            {"id": "q1", "segment_id": "s2"},
            promote=False,
        )
        self.assertEqual(updated["order"][:2], ["s3", "s1"])
        self.assertEqual(updated["order"].count("s2"), 1)

    def test_promote_moves_existing_quote_to_front_without_duplicate(self):
        edl = {
            "rows": [
                {"id": "s1", "checked": True},
                {"id": "s2", "checked": True},
            ],
            "order": ["s1", "s2", "s2"],
        }
        updated = accept_quote(
            edl,
            {"id": "q1", "segment_id": "s2"},
            promote=True,
        )
        self.assertEqual(updated["order"], ["s2", "s1"])

    def test_candidate_status_update_is_copying_and_reject_is_idempotent(self):
        document = {
            "generated_at": "now",
            "candidates": [{"id": "q1", "status": "pending"}],
        }
        rejected = update_candidate_status(document, "q1", "rejected")
        rejected_again = update_candidate_status(rejected, "q1", "rejected")
        self.assertEqual(rejected_again["candidates"][0]["status"], "rejected")
        self.assertEqual(document["candidates"][0]["status"], "pending")
        with self.assertRaises(KeyError):
            update_candidate_status(document, "missing", "accepted")

    def test_analyze_repairs_duplicate_candidate_ids(self):
        result = analyze_quote_candidates(
            _content_map(),
            _segments(),
            chat_json_fn=lambda *_args: {
                "candidates": [
                    {
                        "id": "q1",
                        "topic_id": "t1",
                        "segment_id": segment_id,
                        "type": "claim",
                        "context": "",
                        "reason": "",
                    }
                    for segment_id in ("s1", "s2", "s3")
                ]
            },
            assemble_prompt_fn=lambda: "protocol",
        )
        self.assertEqual(
            [item["id"] for item in result["candidates"]],
            ["q1", "q2", "q3"],
        )
        self.assertIn("id 重复", "\n".join(result["meta"]["warnings"]))


if __name__ == "__main__":
    unittest.main()
