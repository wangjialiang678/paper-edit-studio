import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.models import Transcript, TranscriptSegment, TranscriptToken
from cutpoint_lab.studio.ai_selector import AiSelector, HARD_CONSTRAINTS
from cutpoint_lab.studio.prompt_protocols import MODE_PROTOCOLS
from cutpoint_lab.studio.prompt_store import LEGACY_WARNING, PromptStore


DEFAULT_NAME = "koubo-tighten.md"
DEFAULT_CONTENT = "DEFAULT 剪辑理念：删掉口水话。"
QUALITY_DEFAULT_NAME = "quality-review.md"
QUALITY_DEFAULT_CONTENT = "DEFAULT 质检理念：只纠正有充分上下文证据的错词。"


class _FakeClient:
    def available(self) -> bool:
        return True

    def chat_json(self, *_args, **_kwargs):
        return {"drop": []}


class PromptStoreTests(unittest.TestCase):
    def _store(self, root: Path) -> PromptStore:
        defaults = root / "prompts"
        defaults.mkdir()
        (defaults / DEFAULT_NAME).write_text(DEFAULT_CONTENT, encoding="utf-8")
        (defaults / QUALITY_DEFAULT_NAME).write_text(
            QUALITY_DEFAULT_CONTENT,
            encoding="utf-8",
        )
        return PromptStore(defaults, root / "workspace" / "_settings" / "prompts")

    def test_default_content_is_editable_part_and_assembles_with_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            result = store.get("koubo_tighten")

            self.assertEqual(result["content"], DEFAULT_CONTENT)
            self.assertEqual(result["source"], "default")
            self.assertEqual(result["warnings"], [])
            self.assertFalse(result["legacy"])
            self.assertEqual(result["protocol"], MODE_PROTOCOLS["koubo_tighten"])
            self.assertEqual(
                result["assembled_template"],
                DEFAULT_CONTENT + MODE_PROTOCOLS["koubo_tighten"],
            )
            self.assertEqual(store.assemble("koubo_tighten"), result["assembled_template"])

    def test_override_takes_priority_over_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            updated = store.write("koubo_tighten", "OVERRIDE 更狠地删气口")

            self.assertEqual(updated["source"], "override")
            self.assertEqual(updated["content"], "OVERRIDE 更狠地删气口")
            self.assertEqual(updated["default_content"], DEFAULT_CONTENT)
            self.assertEqual(updated["warnings"], [])
            self.assertEqual(
                updated["assembled_template"],
                "OVERRIDE 更狠地删气口" + MODE_PROTOCOLS["koubo_tighten"],
            )

    def test_legacy_full_override_is_served_verbatim_with_warning(self):
        """旧版全文覆盖层（自带输出协议）：不追加协议、给出迁移警告。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            legacy = "旧版全文\n\n## 输出格式\n\n只输出 JSON…\n\n{{USER_BRIEF}}\n"
            result = store.write("koubo_tighten", legacy)

            self.assertTrue(result["legacy"])
            self.assertEqual(result["assembled_template"], legacy)
            self.assertEqual(result["warnings"], [LEGACY_WARNING])

    def test_reset_is_idempotent_and_restores_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.write("koubo_tighten", "changed")

            self.assertEqual(store.reset("koubo_tighten")["source"], "default")
            self.assertEqual(store.reset("koubo_tighten")["content"], DEFAULT_CONTENT)

    def test_blank_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            with self.assertRaises(ValueError):
                store.write("koubo_tighten", " \n\t")

    def test_ai_selector_renders_workspace_override_with_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            store.write("koubo_tighten", "CUSTOM 只留金句")
            selector = AiSelector(
                root / "prompts",
                client=_FakeClient(),
                workspace_root=root / "workspace",
            )

            rendered = selector._render_system(
                "koubo_tighten",
                brief="保留重点",
                target_duration="30 秒",
            )

            self.assertIn("CUSTOM 只留金句", rendered)
            self.assertIn("保留重点", rendered)          # {{USER_BRIEF}} 在协议尾部被注入
            self.assertIn("## 输出格式", rendered)        # 协议自动追加
            self.assertNotIn("{{USER_BRIEF}}", rendered)  # 占位符全部渲染完毕
            self.assertTrue(rendered.endswith(HARD_CONSTRAINTS))
            self.assertNotIn("DEFAULT", rendered)

    def test_quality_review_default_assembles_editorial_content_with_exact_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))

            result = store.get("quality_review")

            self.assertEqual(result["mode"], "quality_review")
            self.assertEqual(result["source"], "default")
            self.assertEqual(result["content"], QUALITY_DEFAULT_CONTENT)
            self.assertEqual(result["default_content"], QUALITY_DEFAULT_CONTENT)
            self.assertEqual(result["protocol"], MODE_PROTOCOLS["quality_review"])
            self.assertEqual(
                result["assembled_template"],
                QUALITY_DEFAULT_CONTENT + MODE_PROTOCOLS["quality_review"],
            )
            self.assertEqual(
                store.assemble("quality_review"),
                result["assembled_template"],
            )

    def test_ai_selector_rejects_quality_review_mode(self):
        transcript = Transcript(
            source_video="source.mp4",
            duration_ms=1000,
            selected_segment_ids=["s1"],
            segments=[
                TranscriptSegment(
                    id="s1",
                    start_ms=0,
                    end_ms=1000,
                    text="测试",
                    tokens=[
                        TranscriptToken(
                            text="测试",
                            start_ms=0,
                            end_ms=900,
                        )
                    ],
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            selector = AiSelector(
                store.prompts_dir,
                client=_FakeClient(),
                prompt_store=store,
            )

            with self.assertRaisesRegex(ValueError, "未知 AI 模式"):
                selector.suggest(transcript, "quality_review")

    def test_real_repo_prompts_assemble_cleanly(self):
        """仓库真实模板：理念区零协议零占位符，拼装后协议齐全。"""
        repo_prompts = Path(__file__).resolve().parents[1] / "prompts"
        store = PromptStore(repo_prompts, None)
        for mode in ("koubo_tighten", "content_map", "quote_candidates"):
            result = store.get(mode)
            self.assertNotIn("## 输出格式", result["content"], mode)
            self.assertNotIn("{{", result["content"], mode)
            self.assertIn("## 输出格式", result["assembled_template"], mode)
            self.assertIn("{{USER_BRIEF}}", result["assembled_template"], mode)


if __name__ == "__main__":
    unittest.main()
