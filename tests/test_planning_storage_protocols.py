from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.studio.prompt_protocols import MODE_PROTOCOLS
from cutpoint_lab.studio.prompt_store import PromptStore
from cutpoint_lab.studio.workspace import Workspace


class PlanningStorageProtocolTests(unittest.TestCase):
    def test_project_level_planning_documents_roundtrip_outside_cut_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = Workspace(root / "workspace").create_project(
                "planning",
                source_path=root / "source.mp4",
                imported_via="test",
            )
            content_map = {"status": "draft", "topics": []}
            quotes = {"generated_at": "now", "candidates": []}

            project.write_content_map(content_map)
            project.write_quote_candidates(quotes)

            self.assertEqual(project.read_content_map(), content_map)
            self.assertEqual(project.read_quote_candidates(), quotes)
            self.assertEqual(project.content_map_path, project.dir / "content_map.json")
            self.assertEqual(
                project.quote_candidates_path,
                project.dir / "quote_candidates.json",
            )
            self.assertFalse(
                (project.cut_dir("default") / "content_map.json").exists()
            )

    def test_content_map_and_quote_protocols_are_assembled_from_natural_language_prompts(self):
        prompts_dir = Path(__file__).resolve().parents[1] / "prompts"
        store = PromptStore(prompts_dir, None)

        content_map = store.get("content_map")
        quotes = store.get("quote_candidates")

        self.assertIn("主题≠背景", content_map["content"])
        self.assertIn("宁少勿碎", content_map["content"])
        self.assertIn("2–4 个主题", content_map["content"])
        self.assertIn('"claims"', MODE_PROTOCOLS["content_map"])
        self.assertIn('"topics"', content_map["assembled_template"])
        self.assertIn("背景强主张弱", quotes["content"])
        self.assertIn("按强度从高到低", quotes["content"])
        self.assertIn('"candidates"', MODE_PROTOCOLS["quote_candidates"])
        self.assertIn('"segment_id"', quotes["assembled_template"])


if __name__ == "__main__":
    unittest.main()
