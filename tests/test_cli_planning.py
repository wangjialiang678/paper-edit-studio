from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cutpoint_lab.cli import main
from cutpoint_lab.engine import Workspace, read_json, write_json


def _transcript(source: Path) -> dict:
    return {
        "source_video": str(source),
        "duration_ms": 3000,
        "selected_segment_ids": ["s1", "s2", "s3"],
        "segments": [
            {
                "id": "s1",
                "start_ms": 0,
                "end_ms": 800,
                "text": "案例甲",
                "tokens": [{"text": "案例甲", "start_ms": 100, "end_ms": 700}],
            },
            {
                "id": "s2",
                "start_ms": 1000,
                "end_ms": 1800,
                "text": "核心主张",
                "tokens": [{"text": "核心主张", "start_ms": 1100, "end_ms": 1700}],
            },
            {
                "id": "s3",
                "start_ms": 2000,
                "end_ms": 2800,
                "text": "行动",
                "tokens": [{"text": "行动", "start_ms": 2100, "end_ms": 2700}],
            },
        ],
    }


def _project(root: Path):
    source = root / "source.mp4"
    workspace = Workspace(root / "workspace")
    project = workspace.create_project(
        "cli-planning",
        source_path=source,
        imported_via="test",
    )
    write_json(project.transcript_path, _transcript(source))
    project.write_edl(
        "default",
        {
            "rows": [
                {"id": "s1", "checked": True, "text": "案例甲", "role": "background"},
                {"id": "s2", "checked": True, "text": "核心主张", "role": "claim"},
                {"id": "s3", "checked": True, "text": "行动", "role": "filler"},
            ],
            "order": [],
            "brief": {"target_duration_s": 1, "tolerance_s": 0},
        },
    )
    return workspace, project


def _run_json(argv: list[str]) -> tuple[int, dict, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


class CliPlanningTests(unittest.TestCase):
    def test_plan_no_split_runs_shared_pipeline_and_always_creates_new_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, project = _project(root)
            before_default = project.read_edl("default")

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
                    "topic_name": "CLI 模型主题",
                    "title_suggestions": ["CLI 标题"],
                    "decisions": [
                        {"segment_id": item, "keep": True}
                        for item in ids
                    ],
                }

            with patch(
                "cutpoint_lab.cli._planning_pipeline_ai",
                return_value=(chat_json, lambda mode: mode, "mock"),
            ):
                first_code, first, first_stderr = _run_json(
                    [
                        "plan",
                        project.id,
                        "--duration",
                        "3-5",
                        "--intent",
                        "cut_fillers,hook_first",
                        "--brief",
                        "只保留 AI 教育",
                        "--no-split",
                        "--workspace",
                        str(workspace.root),
                        "--json",
                    ]
                )
                second_code, second, _second_stderr = _run_json(
                    [
                        "plan",
                        project.id,
                        "--no-split",
                        "--workspace",
                        str(workspace.root),
                        "--json",
                    ]
                )

            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 0)
            self.assertEqual(first["results"][0]["cuts"], ["ai-plan"])
            self.assertEqual(second["results"][0]["cuts"], ["ai-plan-2"])
            self.assertIn("挑金句", first_stderr)
            self.assertIn("筛选", first_stderr)
            self.assertEqual(project.read_edl("default"), before_default)
            edl = project.read_edl("ai-plan")
            self.assertEqual(edl["label"], "CLI 模型主题")
            self.assertEqual(edl["brief"]["target_duration_s"], 240)
            self.assertEqual(edl["brief"]["tolerance_s"], 60)
            self.assertEqual(
                first["results"][0]["outputs"]["edls"],
                [str(project.cut_dir("ai-plan") / "edl.json")],
            )

    def test_plan_split_creates_one_cut_per_large_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, project = _project(root)

            def chat_json(system: str, user: str) -> dict:
                if system == "content_map":
                    return {
                        "topics": [
                            {
                                "id": "t1",
                                "name": "前半主题",
                                "summary": "",
                                "segment_ids": ["s1", "s2"],
                                "suggested_duration_s": 240,
                                "status": "pending",
                            },
                            {
                                "id": "t2",
                                "name": "后半主题",
                                "summary": "",
                                "segment_ids": ["s3"],
                                "suggested_duration_s": 240,
                                "status": "pending",
                            },
                        ]
                    }
                if system == "quote_candidates":
                    topic_id = "t1" if "[t1]" in user else "t2"
                    segment_id = "s1" if topic_id == "t1" else "s3"
                    return {
                        "candidates": [
                            {
                                "id": f"q-{topic_id}",
                                "topic_id": topic_id,
                                "segment_id": segment_id,
                                "type": "hook",
                                "context": "",
                                "reason": "强",
                            }
                        ]
                    }
                ids = re.findall(r"\[(s\d+)\]", user)
                return {
                    "title_suggestions": [],
                    "decisions": [
                        {"segment_id": item, "keep": True}
                        for item in ids
                    ],
                }

            with patch(
                "cutpoint_lab.cli._planning_pipeline_ai",
                return_value=(chat_json, lambda mode: mode, "mock"),
            ):
                code, manifest, _stderr = _run_json(
                    [
                        "plan",
                        project.id,
                        "--split",
                        "--workspace",
                        str(workspace.root),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(
                manifest["results"][0]["cuts"],
                ["topic-t1", "topic-t2"],
            )
            self.assertTrue(project.content_map_path.is_file())
            self.assertTrue(project.quote_candidates_path.is_file())

    def test_plan_rejects_invalid_duration_range_without_creating_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, project = _project(root)

            code, manifest, _stderr = _run_json(
                [
                    "plan",
                    project.id,
                    "--duration",
                    "5-3",
                    "--workspace",
                    str(workspace.root),
                    "--json",
                ]
            )

            self.assertEqual(code, 1)
            self.assertIn("duration", manifest["results"][0]["error"])
            self.assertEqual(
                [item["name"] for item in project.list_cuts()],
                ["default"],
            )

    def test_content_map_read_is_offline_and_returns_existing_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, project = _project(root)
            document = {
                "generated_at": "now",
                "status": "confirmed",
                "claims": [],
                "backgrounds": [],
                "topics": [],
                "meta": {"source": "human", "model": "", "warnings": []},
            }
            project.write_content_map(document)

            with patch(
                "cutpoint_lab.cli._planning_ai",
                side_effect=AssertionError("只读不应构造 LLM"),
            ):
                code, manifest, _stderr = _run_json(
                    [
                        "content-map",
                        project.id,
                        "--workspace",
                        str(workspace.root),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue(manifest["ok"])
            self.assertEqual(manifest["results"][0]["content_map"], document)

    def test_content_map_analyze_uses_injected_mock_and_writes_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, project = _project(root)

            def chat_json(_system: str, _user: str) -> dict:
                return {
                    "claims": [],
                    "backgrounds": [],
                    "topics": [
                        {
                            "id": "t1",
                            "name": "主题",
                            "summary": "",
                            "segment_ids": ["s1", "s2"],
                            "suggested_duration_s": 10,
                            "status": "confirmed",
                        }
                    ],
                }

            with patch(
                "cutpoint_lab.cli._planning_ai",
                return_value=(chat_json, lambda: "protocol", "mock-model"),
            ):
                code, manifest, _stderr = _run_json(
                    [
                        "content-map",
                        project.id,
                        "--analyze",
                        "--workspace",
                        str(workspace.root),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                manifest["results"][0]["content_map"]["topics"][0]["duration_ms"],
                1600,
            )
            self.assertTrue(project.content_map_path.is_file())

    def test_quotes_analyze_topic_uses_mock_and_preserves_other_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, project = _project(root)
            project.write_content_map(
                {
                    "status": "confirmed",
                    "topics": [
                        {
                            "id": "t1",
                            "name": "一",
                            "summary": "",
                            "segment_ids": ["s1", "s2", "s3"],
                            "status": "confirmed",
                        }
                    ],
                }
            )
            project.write_quote_candidates(
                {
                    "generated_at": "old",
                    "candidates": [
                        {
                            "id": "q1",
                            "topic_id": "other",
                            "segment_id": "s3",
                            "type": "claim",
                            "status": "accepted",
                        }
                    ],
                }
            )

            def chat_json(_system: str, _user: str) -> dict:
                return {
                    "candidates": [
                        {
                            "id": f"q{index}",
                            "topic_id": "t1",
                            "segment_id": segment_id,
                            "type": "claim",
                            "context": "",
                            "reason": "",
                        }
                        for index, segment_id in enumerate(["s1", "s2", "s3"], 1)
                    ]
                }

            with patch(
                "cutpoint_lab.cli._planning_ai",
                return_value=(chat_json, lambda: "protocol", "mock"),
            ):
                code, manifest, _stderr = _run_json(
                    [
                        "quotes",
                        project.id,
                        "--analyze",
                        "--topic",
                        "t1",
                        "--workspace",
                        str(workspace.root),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            ids = {
                item["id"]
                for item in manifest["results"][0]["quotes"]["candidates"]
            }
            self.assertEqual(ids, {"q1", "q1-2", "q2", "q3"})
            self.assertEqual(
                len(ids),
                len(manifest["results"][0]["quotes"]["candidates"]),
            )

    def test_budget_fit_uses_real_plan_and_never_modifies_edl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, project = _project(root)
            before = read_json(project.cut_dir("default") / "edl.json")

            code, manifest, _stderr = _run_json(
                [
                    "budget",
                    project.id,
                    "--cut",
                    "default",
                    "--fit",
                    "strict",
                    "--workspace",
                    str(workspace.root),
                    "--json",
                ]
            )

            self.assertEqual(code, 0)
            result = manifest["results"][0]
            self.assertGreater(result["budget"]["estimated_ms"], 0)
            self.assertIn("suggestions", result["fit"])
            self.assertEqual(
                read_json(project.cut_dir("default") / "edl.json"),
                before,
            )

    def test_budget_requires_cut(self):
        with self.assertRaises(SystemExit) as caught:
            main(["budget", "project", "--json"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
