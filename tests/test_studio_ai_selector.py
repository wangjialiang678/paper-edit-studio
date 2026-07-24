import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.models import Transcript, TranscriptSegment, TranscriptToken
from cutpoint_lab.studio.ai_selector import AiSelector
from cutpoint_lab.studio.llm_client import LlmError, extract_json_object

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def _transcript() -> Transcript:
    segments = [
        TranscriptSegment(
            id=f"sentence_{index:04d}",
            start_ms=index * 2000,
            end_ms=index * 2000 + 1500,
            text=f"第 {index} 句话",
            tokens=[TranscriptToken(text=f"第{index}", start_ms=index * 2000 + 10, end_ms=index * 2000 + 500)],
        )
        for index in range(1, 5)
    ]
    return Transcript(
        source_video="source.mp4",
        duration_ms=10000,
        selected_segment_ids=[segment.id for segment in segments],
        segments=segments,
    )


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def available(self) -> bool:
        return True

    def chat_json(self, system, user, **_kwargs):
        self.calls.append((system, user))
        return self.responses.pop(0)


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(extract_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_json_with_surrounding_text(self):
        self.assertEqual(extract_json_object('结果如下 {"a": {"b": "x}y"}} 完'), {"a": {"b": "x}y"}})

    def test_invalid_raises(self):
        with self.assertRaises(LlmError):
            extract_json_object("完全没有 JSON")


class KouboSelectorTests(unittest.TestCase):
    def test_drop_is_inverted_to_decisions_and_unknown_ids_are_ignored(self):
        client = FakeClient(
            [
                {
                    "summary": "整体去水",
                    "drop": [
                        {"id": "sentence_0001", "reason": "寒暄"},
                        {"id": "sentence_9999", "reason": "编造"},
                    ],
                }
            ]
        )
        selector = AiSelector(PROMPTS_DIR, client=client)
        suggestion = selector.suggest(_transcript(), "koubo_tighten")
        decisions = {item["segment_id"]: item for item in suggestion.payload["decisions"]}
        self.assertEqual(len(decisions), 4)
        self.assertFalse(decisions["sentence_0001"]["keep"])
        self.assertEqual(decisions["sentence_0001"]["reason"], "寒暄")
        self.assertTrue(decisions["sentence_0002"]["keep"])
        self.assertEqual(decisions["sentence_0002"]["reason"], "")
        self.assertNotIn("sentence_9999", decisions)
        self.assertIn("sentence_0002", suggestion.payload["keep_segment_ids"])
        self.assertTrue(any("9999" in warning for warning in suggestion.warnings))
        self.assertGreater(suggestion.payload["keep_duration_ms"], 0)
        system, user = client.calls[0]
        self.assertIn("硬约束", system)
        self.assertIn("[sentence_0001]", user)

    def test_empty_drop_keeps_all_and_all_drop_removes_all(self):
        empty = AiSelector(PROMPTS_DIR, client=FakeClient([{"drop": []}])).suggest(
            _transcript(),
            "koubo_tighten",
        )
        self.assertEqual(
            empty.payload["keep_segment_ids"],
            [f"sentence_{index:04d}" for index in range(1, 5)],
        )
        self.assertTrue(all(item["keep"] for item in empty.payload["decisions"]))
        self.assertTrue(
            all(item["reason"] == "" for item in empty.payload["decisions"])
        )

        dropped = AiSelector(
            PROMPTS_DIR,
            client=FakeClient(
                [
                    {
                        "drop": [
                            {
                                "id": f"sentence_{index:04d}",
                                "reason": "删除",
                            }
                            for index in range(1, 5)
                        ]
                    }
                ]
            ),
        ).suggest(_transcript(), "koubo_tighten")
        self.assertEqual(dropped.payload["keep_segment_ids"], [])
        self.assertTrue(
            all(not item["keep"] for item in dropped.payload["decisions"])
        )

    def test_brief_rendered_into_system(self):
        client = FakeClient([{"drop": []}])
        selector = AiSelector(PROMPTS_DIR, client=client)
        selector.suggest(_transcript(), "koubo_tighten", brief="只保留 AI 教育相关")
        system, _ = client.calls[0]
        self.assertIn("只保留 AI 教育相关", system)
        self.assertNotIn("{{USER_BRIEF}}", system)


class RetiredSelectorModesTests(unittest.TestCase):
    def test_topic_slicing_and_highlight_remix_are_retired(self):
        selector = AiSelector(PROMPTS_DIR, client=FakeClient([]))
        for mode in ("topic_slicing", "highlight_remix"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, "已并入 AI 出剪辑方案"):
                    selector.suggest(_transcript(), mode)


class ChunkResilienceTests(unittest.TestCase):
    """单块 LLM 失败：重试一次成功则无痕；两次失败降级默认保留，不毁整次分析。"""

    def test_chunk_retry_once_recovers(self):
        calls = {"n": 0}

        class FlakyClient:
            def available(self):
                return True

            def chat_json(self, _system, _user, **_kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise LlmError("LLM 输出中找不到 JSON：[0.5]")
                return {"drop": []}

        selector = AiSelector(PROMPTS_DIR, client=FlakyClient())
        suggestion = selector.suggest(_transcript(), "koubo_tighten")
        self.assertEqual(calls["n"], 2)
        self.assertFalse(any("失败" in w for w in suggestion.warnings))
        self.assertEqual(len(suggestion.payload["keep_segment_ids"]), 4)

    def test_chunk_double_failure_degrades_to_keep_all(self):
        class DeadClient:
            def available(self):
                return True

            def chat_json(self, *_args, **_kwargs):
                raise LlmError("LLM 输出中找不到 JSON：[0.5]")

        selector = AiSelector(PROMPTS_DIR, client=DeadClient())
        suggestion = selector.suggest(_transcript(), "koubo_tighten")
        decisions = suggestion.payload["decisions"]
        self.assertEqual(len(decisions), 4)
        self.assertTrue(all(d["keep"] for d in decisions))
        self.assertTrue(any("AI 调用失败" in w for w in suggestion.warnings))


class SegmentIdRepairTests(unittest.TestCase):
    """模型简写 segment_id（0055 / 55 / sentence_55）应被确定性还原，歧义与未知仍拒绝。"""

    def test_alias_map_and_resolve(self):
        from cutpoint_lab.studio.ai_selector import _alias_map, _resolve_id

        aliases = _alias_map(["sentence_0055", "sentence_0102"])
        self.assertEqual(_resolve_id("sentence_0055", aliases), "sentence_0055")
        self.assertEqual(_resolve_id("0055", aliases), "sentence_0055")
        self.assertEqual(_resolve_id(55, aliases), "sentence_0055")
        self.assertEqual(_resolve_id("sentence_55", aliases), "sentence_0055")
        self.assertEqual(_resolve_id("102", aliases), "sentence_0102")
        # 前缀被写坏但数字尾巴还在（实测 glm 产出）→ 仍可确定性还原
        self.assertEqual(_resolve_id("性_0055", aliases), "sentence_0055")
        self.assertEqual(_resolve_id("游戏_id_0102", aliases), "sentence_0102")
        self.assertIsNone(_resolve_id("9999", aliases))
        self.assertIsNone(_resolve_id("坏前缀_9999", aliases))
        self.assertIsNone(_resolve_id(None, aliases))

    def test_ambiguous_alias_dropped(self):
        from cutpoint_lab.studio.ai_selector import _alias_map, _resolve_id

        # 两个 id 数字部分相同（不同前缀）→ "7" 歧义，须拒绝；全称仍可解析
        aliases = _alias_map(["a_0007", "b_0007"])
        self.assertIsNone(_resolve_id("0007", aliases))
        self.assertEqual(_resolve_id("a_0007", aliases), "a_0007")

    def test_koubo_repairs_shorthand_ids_without_warnings(self):
        class ShorthandClient:
            def available(self):
                return True

            def chat_json(self, _system, _user, **_kwargs):
                return {"drop": [
                    {"id": "0002", "reason": "简写形式"},
                ]}

        selector = AiSelector(PROMPTS_DIR, client=ShorthandClient())
        suggestion = selector.suggest(_transcript(), "koubo_tighten")
        by_id = {d["segment_id"]: d for d in suggestion.payload["decisions"]}
        self.assertTrue(by_id["sentence_0001"]["keep"])
        self.assertEqual(by_id["sentence_0001"]["labels"], [])
        self.assertFalse(any("未知" in w or "未覆盖" in w for w in suggestion.warnings))


class PromptFilesTests(unittest.TestCase):
    def test_prompt_files_are_pure_editorial_and_assemble_with_constraints(self):
        """提示词文件=纯自然语言剪辑理念；协议（输出格式/硬约束/占位符）在拼装层追加。"""
        from cutpoint_lab.studio.prompt_store import PromptStore

        store = PromptStore(PROMPTS_DIR, None)
        for name in ("koubo-tighten.md", "content-map.md", "quote-candidates.md"):
            content = (PROMPTS_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("## 输出格式", content, name)
            self.assertNotIn("{{", content, name)
        for mode in ("koubo_tighten", "content_map", "quote_candidates"):
            assembled = store.assemble(mode)
            self.assertIn("segment_id", assembled)
            self.assertIn("硬约束", assembled)
            self.assertIn("{{USER_BRIEF}}", assembled)

    def test_content_map_prompt_enforces_large_complete_topics(self):
        content = (PROMPTS_DIR / "content-map.md").read_text(encoding="utf-8")
        self.assertIn("可独立成片的完整叙事", content)
        self.assertIn("宁少勿碎", content)
        self.assertIn("3–15 分钟", content)
        self.assertIn("15 分钟", content)
        self.assertIn("2–4 个主题", content)
        self.assertIn("不足 3 分钟", content)


if __name__ == "__main__":
    unittest.main()
