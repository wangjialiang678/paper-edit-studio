from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from cutpoint_lab.io import read_json, write_json
from cutpoint_lab.studio.prompt_protocols import MODE_PROTOCOLS
from cutpoint_lab.studio.prompt_store import PromptStore
from cutpoint_lab.studio.server import StudioApplication, bind_server
from cutpoint_lab.studio.workspace import Workspace


PROMPT_TEXT = """# 文稿对齐裁决

你是一名剪辑助理。用户提供了一段成片文稿，系统未能自动在原视频字幕中找到足够相似的句子。你的任务：判断这段文稿对应字幕中的哪句/哪几句（可能被改写过），或确认字幕中根本没有对应内容。

## 判断标准

- 只看语义对应：文稿段落若是某几句字幕的改写/缩写/合并，找出那些句子。
- 对应关系必须有实质语义重叠；仅主题相近不算对应。
- 字幕中确实没有对应内容时，明确说没有——宁可判无，不可硬凑。
"""


def _transcript(source: Path) -> dict:
    return {
        "source_video": str(source),
        "duration_ms": 600,
        "selected_segment_ids": ["s1", "s2"],
        "segments": [
            {
                "id": "s1",
                "start_ms": 0,
                "end_ms": 250,
                "text": "第一句",
                "tokens": [{"text": "第一句", "start_ms": 0, "end_ms": 250}],
            },
            {
                "id": "s2",
                "start_ms": 300,
                "end_ms": 550,
                "text": "第二句",
                "tokens": [{"text": "第二句", "start_ms": 300, "end_ms": 550}],
            },
        ],
    }


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read().decode())


class _ComposeClient:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str, **_kwargs) -> dict:
        self.calls.append((system, user))
        return {
            "matches": [
                {
                    "paragraph_index": 0,
                    "segment_ids": ["s2"],
                    "confidence": 0.91,
                    "reason": "第二句的轻微改写",
                }
            ]
        }


class _ComposeSelector:
    def __init__(self):
        self.client = _ComposeClient()

    def available(self) -> bool:
        return True


class ComposePromptTests(unittest.TestCase):
    def test_prompt_philosophy_is_exact_and_protocol_is_assembled(self):
        prompts_dir = Path(__file__).resolve().parents[1] / "prompts"
        store = PromptStore(prompts_dir, None)

        result = store.get("compose_align")

        self.assertEqual(result["content"], PROMPT_TEXT)
        self.assertEqual(result["protocol"], MODE_PROTOCOLS["compose_align"])
        self.assertIn('"matches"', result["assembled_template"])
        self.assertIn("{{USER_BRIEF}}", result["assembled_template"])


class ComposeScriptHttpTests(unittest.TestCase):
    def test_ai_flag_uses_quality_llm_and_assembled_compose_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            selector = _ComposeSelector()
            app = StudioApplication(
                Workspace(root / "workspace"),
                selector=selector,
                auto_ai=False,
            )
            project = app.workspace.create_project(
                "compose-ai", source_path=source, imported_via="test"
            )
            write_json(project.transcript_path, _transcript(source))

            created = app.create_cut_from_script(
                project,
                {
                    "name": "ai-cut",
                    "script": "第贰句",
                    "ai": True,
                },
            )

            self.assertEqual(created["report"]["paragraphs"][0]["status"], "ai")
            self.assertEqual(project.read_edl("ai-cut")["order"], ["s2"])
            self.assertEqual(len(selector.client.calls), 1)
            system, user = selector.client.calls[0]
            self.assertIn("# 文稿对齐裁决", system)
            self.assertIn('"matches"', system)
            self.assertNotIn("{{USER_BRIEF}}", system)
            self.assertIn("[s2] 第二句", user)

    def test_from_script_creates_cut_persists_report_and_conflicts_with_409(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            app = StudioApplication(Workspace(root / "workspace"), auto_ai=False)
            project = app.workspace.create_project(
                "compose", source_path=source, imported_via="test"
            )
            write_json(project.transcript_path, _transcript(source))
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            payload = {
                "name": "script-cut",
                "label": "外部文稿版",
                "script": "第二句\n第一句",
                "ai": False,
            }
            try:
                status, created = _request_json(
                    f"{base}/cuts/from-script", method="POST", payload=payload
                )
                self.assertEqual(status, 200)
                self.assertTrue(created["ok"])
                self.assertEqual(created["cut"]["name"], "script-cut")
                self.assertEqual(project.read_edl("script-cut")["order"], ["s2", "s1"])
                report_path = project.cut_dir("script-cut") / "compose_report.json"
                self.assertEqual(read_json(report_path), created["report"])

                status, report = _request_json(
                    f"{base}/cuts/script-cut/compose-report"
                )
                self.assertEqual(status, 200)
                self.assertEqual(report, created["report"])

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(
                        f"{base}/cuts/from-script", method="POST", payload=payload
                    )
                self.assertEqual(caught.exception.code, 409)
                caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
