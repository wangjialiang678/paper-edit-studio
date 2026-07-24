from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.planning.pipeline import (
    INTENT_PRESETS,
    PlanPipelineError,
    generate_plans,
)
from cutpoint_lab.studio.workspace import Workspace


def _segments() -> list[dict]:
    return [
        {
            "id": f"s{index}",
            "start_ms": (index - 1) * 1000,
            "end_ms": index * 1000,
            "text": f"第 {index} 句",
        }
        for index in range(1, 5)
    ]


class _ProjectSink:
    def __init__(self, root: Path):
        self.project = Workspace(root / "workspace").create_project(
            "pipeline",
            source_path=root / "source.mp4",
            imported_via="test",
        )
        self.project.write_edl(
            "default",
            {
                "rows": [{"id": "manual", "checked": True, "text": "人工成果"}],
                "order": [],
            },
        )
        self.content_map = None
        self.quotes = None

    def cut_names(self) -> list[str]:
        return [item["name"] for item in self.project.list_cuts()]

    def create_cut(self, name: str, label: str, edl: dict) -> str:
        return self.project.create_cut(name, label, edl)["name"]

    def write_content_map(self, document: dict) -> None:
        self.content_map = document
        self.project.write_content_map(document)

    def write_quotes(self, document: dict) -> None:
        self.quotes = document
        self.project.write_quote_candidates(document)


def _run(
    sink: _ProjectSink,
    chat_json,
    *,
    split_topics: bool,
    progress=None,
) -> dict:
    return generate_plans(
        _segments(),
        intent=["cut_fillers", "hook_first"],
        intent_extra="只保留 AI 教育",
        duration_min_s=180,
        duration_max_s=300,
        split_topics=split_topics,
        chat_json_fn=chat_json,
        assemble_prompt_fn=lambda mode: mode,
        list_cut_names_fn=sink.cut_names,
        create_cut_fn=sink.create_cut,
        write_content_map_fn=sink.write_content_map,
        write_quote_candidates_fn=sink.write_quotes,
        progress_fn=progress,
        model="mock",
    )


class PlanningPipelineTests(unittest.TestCase):
    def test_intent_presets_are_backend_owned_and_include_defaults(self):
        by_key = {item["key"]: item for item in INTENT_PRESETS}
        self.assertEqual(
            set(by_key),
            {
                "cut_fillers",
                "hook_first",
                "keep_insights",
                "keep_stories",
                "cut_smalltalk",
                "keep_data",
            },
        )
        self.assertTrue(by_key["cut_fillers"]["default"])
        self.assertTrue(by_key["hook_first"]["default"])
        self.assertFalse(by_key["keep_data"]["default"])
        self.assertTrue(all(item["brief"] for item in INTENT_PRESETS))

    def test_whole_video_always_creates_new_ai_plan_without_overwriting_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = _ProjectSink(Path(tmp))

            def chat_json(system: str, user: str) -> dict:
                if system == "quote_candidates":
                    return {
                        "candidates": [
                            {
                                "id": "q1",
                                "topic_id": "whole",
                                "segment_id": "s2",
                                "type": "hook",
                                "context": "",
                                "reason": "最强",
                            }
                        ]
                    }
                ids = re.findall(r"\[(s\d+)\]", user)
                return {
                    "topic_name": "AI 教育的关键变化",
                    "title_suggestions": ["教育正在发生什么", "老师如何使用 AI"],
                    "decisions": [
                        {
                            "segment_id": segment_id,
                            "keep": segment_id in {"s1", "s2"},
                            "reason": "测试",
                            "labels": [],
                        }
                        for segment_id in ids
                    ],
                }

            first = _run(sink, chat_json, split_topics=False)
            second = _run(sink, chat_json, split_topics=False)

            self.assertEqual(first["cuts"], ["ai-plan"])
            self.assertEqual(second["cuts"], ["ai-plan-2"])
            self.assertEqual(
                sink.project.read_edl("default")["rows"][0]["text"],
                "人工成果",
            )
            first_edl = sink.project.read_edl("ai-plan")
            self.assertEqual(first_edl["label"], "AI 教育的关键变化")
            self.assertEqual(first_edl["order"], ["s2", "s1", "s2"])
            self.assertEqual(
                first_edl["brief"],
                {
                    "claim": "AI 教育的关键变化",
                    "intent": ["cut_fillers", "hook_first"],
                    "intent_extra": "只保留 AI 教育",
                    "target_duration_s": 240,
                    "tolerance_s": 60,
                    "title_suggestions": [
                        "教育正在发生什么",
                        "老师如何使用 AI",
                    ],
                },
            )
            self.assertFalse(sink.project.content_map_path.exists())
            self.assertEqual(
                sink.quotes["candidates"][0]["status"],
                "accepted",
            )

    def test_split_topics_writes_draft_map_candidates_cuts_and_real_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = _ProjectSink(Path(tmp))
            events: list[dict] = []

            def chat_json(system: str, user: str) -> dict:
                if system == "content_map":
                    return {
                        "claims": [],
                        "backgrounds": [],
                        "topics": [
                            {
                                "id": "t1",
                                "name": "主题一",
                                "summary": "前半",
                                "segment_ids": ["s1", "s2"],
                                "suggested_duration_s": 240,
                                "status": "pending",
                            },
                            {
                                "id": "t2",
                                "name": "主题二",
                                "summary": "后半",
                                "segment_ids": ["s3", "s4"],
                                "suggested_duration_s": 240,
                                "status": "pending",
                            },
                        ],
                    }
                if system == "quote_candidates":
                    topic_id = "t1" if "[t1]" in user else "t2"
                    segment_id = "s1" if topic_id == "t1" else "s3"
                    return {
                        "candidates": [
                            {
                                "id": "q1",
                                "topic_id": topic_id,
                                "segment_id": segment_id,
                                "type": "hook",
                                "context": "",
                                "reason": "测试",
                            }
                        ]
                    }
                ids = re.findall(r"\[(s\d+)\]", user)
                return {
                    "title_suggestions": ["标题"],
                    "decisions": [
                        {
                            "segment_id": segment_id,
                            "keep": True,
                            "reason": "测试",
                            "labels": [],
                        }
                        for segment_id in ids
                    ],
                }

            result = _run(
                sink,
                chat_json,
                split_topics=True,
                progress=events.append,
            )

            self.assertEqual(result["cuts"], ["topic-t1", "topic-t2"])
            self.assertEqual(sink.content_map["status"], "draft")
            self.assertTrue(
                all(topic["status"] == "confirmed" for topic in sink.content_map["topics"])
            )
            self.assertEqual(len(sink.quotes["candidates"]), 2)
            self.assertEqual(
                [item["id"] for item in sink.quotes["candidates"]],
                ["q1", "q1-2"],
            )
            self.assertTrue(
                all(
                    item["status"] == "accepted"
                    for item in sink.quotes["candidates"]
                )
            )
            self.assertEqual(sink.project.read_edl("topic-t1")["label"], "主题一")
            self.assertEqual(sink.project.read_edl("topic-t2")["label"], "主题二")
            self.assertEqual(
                sink.project.read_edl("topic-t1")["order"],
                ["s1", "s1", "s2"],
            )
            self.assertEqual(
                {event["stage"] for event in events},
                {"topics", "quotes", "select"},
            )
            self.assertEqual(
                [event["topics_done"] for event in events if event["stage"] == "quotes"],
                [0, 1, 2],
            )
            self.assertEqual(
                [event["topics_done"] for event in events if event["stage"] == "select"],
                [0, 1, 2],
            )

    def test_quote_failure_degrades_and_selection_failure_skips_only_that_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = _ProjectSink(Path(tmp))

            def chat_json(system: str, user: str) -> dict:
                if system == "content_map":
                    return {
                        "topics": [
                            {
                                "id": "t1",
                                "name": "可用主题",
                                "summary": "",
                                "segment_ids": ["s1", "s2"],
                                "suggested_duration_s": 240,
                                "status": "pending",
                            },
                            {
                                "id": "t2",
                                "name": "失败主题",
                                "summary": "",
                                "segment_ids": ["s3", "s4"],
                                "suggested_duration_s": 240,
                                "status": "pending",
                            },
                        ]
                    }
                if system == "quote_candidates":
                    if "[t1]" in user:
                        raise RuntimeError("quotes unavailable")
                    return {"candidates": []}
                if "[s3]" in user:
                    raise RuntimeError("selection unavailable")
                return {
                    "decisions": [
                        {"segment_id": "s1", "keep": True},
                        {"segment_id": "s2", "keep": True},
                    ],
                    "title_suggestions": [],
                }

            result = _run(sink, chat_json, split_topics=True)

            self.assertEqual(result["cuts"], ["topic-t1"])
            warning_text = "\n".join(result["warnings"])
            self.assertIn("可用主题", warning_text)
            self.assertIn("金句", warning_text)
            self.assertIn("失败主题", warning_text)
            self.assertIn("已跳过", warning_text)
            self.assertEqual(
                sink.project.read_edl("topic-t1")["order"],
                ["s1", "s2"],
            )

    def test_all_selection_failures_raise_pipeline_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = _ProjectSink(Path(tmp))

            def chat_json(system: str, _user: str) -> dict:
                if system == "quote_candidates":
                    return {"candidates": []}
                raise RuntimeError("selection down")

            with self.assertRaisesRegex(PlanPipelineError, "全部主题"):
                _run(sink, chat_json, split_topics=False)
            self.assertEqual(sink.cut_names(), ["default"])

    def test_selection_prompt_contains_intents_duration_and_title_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = _ProjectSink(Path(tmp))
            systems: list[str] = []

            def chat_json(system: str, _user: str) -> dict:
                if system == "quote_candidates":
                    return {"candidates": []}
                systems.append(system)
                return {
                    "decisions": [{"segment_id": f"s{i}", "keep": True} for i in range(1, 5)],
                    "title_suggestions": ["标题"],
                }

            _run(sink, chat_json, split_topics=False)

            rendered = systems[0]
            self.assertIn("删口癖", rendered)
            self.assertIn("开头放钩子金句", rendered)
            self.assertIn("只保留 AI 教育", rendered)
            self.assertIn("180–300 秒", rendered)
            self.assertIn("宁紧勿超", rendered)
            self.assertIn("title_suggestions", rendered)
            self.assertIn("topic_name", rendered)


if __name__ == "__main__":
    unittest.main()
