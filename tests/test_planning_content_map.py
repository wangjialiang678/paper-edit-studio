from __future__ import annotations

import re
import unittest

from cutpoint_lab.planning.content_map import (
    analyze_content_map,
    validate_content_map,
)


def _segments(count: int = 3) -> list[dict]:
    return [
        {
            "id": f"sentence_{index:04d}",
            "start_ms": (index - 1) * 1000,
            "end_ms": index * 1000,
            "text": f"第 {index} 句",
        }
        for index in range(1, count + 1)
    ]


class ContentMapPlanningTests(unittest.TestCase):
    def test_analyze_repairs_ids_enforces_single_topic_ownership_and_recomputes_duration(self):
        calls: list[tuple[str, str]] = []

        def chat_json(system: str, user: str) -> dict:
            calls.append((system, user))
            return {
                "claims": [
                    {
                        "id": "c1",
                        "text": "主张",
                        "segment_ids": ["sentence_1", "坏前缀_0002", "sentence_9999"],
                        "reason": "可传播",
                    }
                ],
                "backgrounds": [
                    {
                        "id": "b1",
                        "text": "设计营",
                        "segment_ids": ["2"],
                        "kind": "event",
                    }
                ],
                "topics": [
                    {
                        "id": "t1",
                        "name": "主题一",
                        "summary": "前两句",
                        "segment_ids": ["1", "2"],
                        "duration_ms": 999999,
                        "suggested_duration_s": 60,
                        "status": "confirmed",
                    },
                    {
                        "id": "t2",
                        "name": "主题二",
                        "summary": "后两句",
                        "segment_ids": ["2", "3"],
                        "duration_ms": 1,
                        "suggested_duration_s": 30,
                        "status": "pending",
                    },
                ],
            }

        result = analyze_content_map(
            _segments(),
            chat_json_fn=chat_json,
            assemble_prompt_fn=lambda: "CONTENT MAP PROTOCOL",
            model="mock-model",
            now_fn=lambda: "2026-07-23T10:00:00",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "CONTENT MAP PROTOCOL")
        self.assertIn("[sentence_0001]", calls[0][1])
        self.assertEqual(result["generated_at"], "2026-07-23T10:00:00")
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["claims"][0]["segment_ids"], ["sentence_0001", "sentence_0002"])
        self.assertEqual(result["topics"][0]["segment_ids"], ["sentence_0001", "sentence_0002"])
        self.assertEqual(result["topics"][0]["duration_ms"], 2000)
        self.assertEqual(result["topics"][1]["segment_ids"], ["sentence_0003"])
        self.assertEqual(result["topics"][1]["duration_ms"], 1000)
        self.assertEqual(result["meta"]["source"], "ai")
        self.assertEqual(result["meta"]["model"], "mock-model")
        warnings = "\n".join(result["meta"]["warnings"])
        self.assertIn("sentence_9999", warnings)
        self.assertIn("sentence_0002", warnings)

    def test_validate_rejects_unknown_ids_and_cross_topic_conflicts(self):
        payload = {
            "status": "confirmed",
            "claims": [],
            "backgrounds": [],
            "topics": [
                {
                    "id": "t1",
                    "name": "一",
                    "summary": "",
                    "segment_ids": ["sentence_0001", "sentence_0002"],
                    "suggested_duration_s": 10,
                    "status": "confirmed",
                },
                {
                    "id": "t2",
                    "name": "二",
                    "summary": "",
                    "segment_ids": ["sentence_0002"],
                    "suggested_duration_s": 10,
                    "status": "confirmed",
                },
            ],
        }
        with self.assertRaisesRegex(ValueError, "sentence_0002.*t1.*t2"):
            validate_content_map(payload, _segments(), source="human")

        payload["topics"][1]["segment_ids"] = ["sentence_9999"]
        with self.assertRaisesRegex(ValueError, "sentence_9999"):
            validate_content_map(payload, _segments(), source="human")

    def test_validate_recomputes_duration_and_marks_human_source(self):
        result = validate_content_map(
            {
                "generated_at": "old",
                "status": "confirmed",
                "claims": [],
                "backgrounds": [],
                "topics": [
                    {
                        "id": "t1",
                        "name": "主题",
                        "summary": "",
                        "segment_ids": ["sentence_0002", "sentence_0003"],
                        "duration_ms": 9,
                        "suggested_duration_s": 12,
                        "status": "confirmed",
                    }
                ],
                "meta": {"source": "ai", "model": "old", "warnings": ["keep"]},
            },
            _segments(),
            source="human",
            now_fn=lambda: "2026-07-23T11:00:00",
        )
        self.assertEqual(result["topics"][0]["duration_ms"], 2000)
        self.assertEqual(result["generated_at"], "2026-07-23T11:00:00")
        self.assertEqual(result["meta"]["source"], "human")
        self.assertEqual(result["meta"]["warnings"], ["keep"])

    def test_ai_duplicate_entity_ids_are_repaired_to_unique_ids(self):
        result = analyze_content_map(
            _segments(),
            chat_json_fn=lambda *_args: {
                "claims": [
                    {"id": "c1", "text": "一", "segment_ids": ["s1"], "reason": ""},
                    {"id": "c1", "text": "二", "segment_ids": ["s2"], "reason": ""},
                ],
                "backgrounds": [],
                "topics": [
                    {
                        "id": "t1",
                        "name": "一",
                        "summary": "",
                        "segment_ids": ["s1"],
                        "suggested_duration_s": 1,
                        "status": "pending",
                    },
                    {
                        "id": "t1",
                        "name": "二",
                        "summary": "",
                        "segment_ids": ["s2"],
                        "suggested_duration_s": 1,
                        "status": "pending",
                    },
                ],
            },
            assemble_prompt_fn=lambda: "protocol",
            now_fn=lambda: "now",
        )
        self.assertEqual([item["id"] for item in result["claims"]], ["c1", "c2"])
        self.assertEqual([item["id"] for item in result["topics"]], ["t1", "t2"])
        self.assertIn("id 重复", "\n".join(result["meta"]["warnings"]))

    def test_long_video_chunks_retries_once_and_runs_one_merge_call(self):
        calls: list[str] = []
        first_failed = False

        def chat_json(_system: str, user: str) -> dict:
            nonlocal first_failed
            calls.append(user)
            if "分块主题摘要" in user:
                ids = list(dict.fromkeys(re.findall(r"sentence_\d{4}", user)))
                return {
                    "topics": [
                        {
                            "id": "merged",
                            "name": "合并主题",
                            "summary": "跨块归纳",
                            "segment_ids": ids,
                            "suggested_duration_s": 90,
                            "status": "pending",
                        }
                    ]
                }
            ids = re.findall(r"\[(sentence_\d{4})\]", user)
            if not first_failed:
                first_failed = True
                raise RuntimeError("transient")
            return {
                "claims": [],
                "backgrounds": [],
                "topics": [
                    {
                        "id": f"chunk-{ids[0]}",
                        "name": "块主题",
                        "summary": "摘要",
                        "segment_ids": ids,
                        "suggested_duration_s": 30,
                        "status": "pending",
                    }
                ],
            }

        result = analyze_content_map(
            _segments(151),
            chat_json_fn=chat_json,
            assemble_prompt_fn=lambda: "protocol",
            model="mock",
            now_fn=lambda: "now",
        )

        self.assertEqual(len(calls), 4)  # 首块失败+重试、第二块、一次合并。
        self.assertEqual(sum("分块主题摘要" in call for call in calls), 1)
        self.assertEqual(result["topics"][0]["id"], "merged")
        self.assertEqual(len(result["topics"][0]["segment_ids"]), 151)
        self.assertEqual(result["topics"][0]["duration_ms"], 151_000)

    def test_long_video_double_failures_degrade_to_pending_chunk_topics(self):
        calls = 0

        def chat_json(_system: str, _user: str) -> dict:
            nonlocal calls
            calls += 1
            raise RuntimeError("always fails")

        result = analyze_content_map(
            _segments(151),
            chat_json_fn=chat_json,
            assemble_prompt_fn=lambda: "protocol",
            model="mock",
            now_fn=lambda: "now",
        )

        self.assertEqual(calls, 6)  # 两块各两次 + 合并两次。
        self.assertEqual(len(result["topics"]), 2)
        self.assertTrue(all(item["status"] == "pending" for item in result["topics"]))
        assigned = [
            segment_id
            for topic in result["topics"]
            for segment_id in topic["segment_ids"]
        ]
        self.assertEqual(len(assigned), 151)
        warnings = "\n".join(result["meta"]["warnings"])
        self.assertIn("AI 调用失败", warnings)
        self.assertIn("跨块主题合并失败", warnings)


if __name__ == "__main__":
    unittest.main()
