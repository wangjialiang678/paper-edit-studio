import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.studio.ai_selector import AiSelector, HARD_CONSTRAINTS
from cutpoint_lab.studio.prompt_store import PromptStore


DEFAULT_NAME = "koubo-tighten.md"
DEFAULT_CONTENT = "DEFAULT {{USER_BRIEF}} {{TARGET_DURATION}}"


class _FakeClient:
    def available(self) -> bool:
        return True

    def chat_json(self, *_args, **_kwargs):
        return {"decisions": []}


class PromptStoreTests(unittest.TestCase):
    def _store(self, root: Path) -> PromptStore:
        defaults = root / "prompts"
        defaults.mkdir()
        (defaults / DEFAULT_NAME).write_text(DEFAULT_CONTENT, encoding="utf-8")
        return PromptStore(defaults, root / "workspace" / "_settings" / "prompts")

    def test_override_takes_priority_over_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            default = store.get("koubo_tighten")
            self.assertEqual(default["content"], DEFAULT_CONTENT)
            self.assertEqual(default["source"], "default")

            updated = store.write(
                "koubo_tighten",
                "OVERRIDE {{USER_BRIEF}} {{TARGET_DURATION}}",
            )
            self.assertEqual(updated["source"], "override")
            self.assertEqual(updated["content"], "OVERRIDE {{USER_BRIEF}} {{TARGET_DURATION}}")
            self.assertEqual(updated["default_content"], DEFAULT_CONTENT)

    def test_reset_is_idempotent_and_restores_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.write("koubo_tighten", "changed")

            self.assertEqual(store.reset("koubo_tighten")["source"], "default")
            self.assertEqual(store.reset("koubo_tighten")["content"], DEFAULT_CONTENT)

    def test_missing_placeholders_return_warnings_without_rejecting_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            result = store.write("koubo_tighten", "仍然允许保存")

            self.assertEqual(len(result["warnings"]), 2)
            self.assertTrue(any("{{USER_BRIEF}}" in warning for warning in result["warnings"]))
            self.assertTrue(any("{{TARGET_DURATION}}" in warning for warning in result["warnings"]))

    def test_blank_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            with self.assertRaises(ValueError):
                store.write("koubo_tighten", " \n\t")

    def test_ai_selector_renders_workspace_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            store.write(
                "koubo_tighten",
                "CUSTOM {{USER_BRIEF}} duration={{TARGET_DURATION}}",
            )
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

            self.assertIn("CUSTOM", rendered)
            self.assertIn("保留重点", rendered)
            self.assertIn("duration=30 秒", rendered)
            self.assertTrue(rendered.endswith(HARD_CONSTRAINTS))
            self.assertNotIn("DEFAULT", rendered)


if __name__ == "__main__":
    unittest.main()
