from __future__ import annotations

import unittest

try:
    from cutpoint_lab.studio.span_match import match_span
except ImportError:
    match_span = None


class SpanMatchTests(unittest.TestCase):
    def _match(self, tokens, span_text, occupied=None):
        self.assertIsNotNone(match_span, "span_match 模块尚未实现")
        assert match_span is not None
        return match_span(tokens, span_text, occupied)

    def test_matches_pure_chinese_token(self):
        tokens = [{"text": "我们"}, {"text": "就是"}, {"text": "这样"}]

        self.assertEqual(self._match(tokens, "就是"), (1, 1))

    def test_matches_mixed_chinese_and_ascii_with_inserted_space(self):
        tokens = [{"text": "用"}, {"text": "Open"}, {"text": "AI"}, {"text": "做视频"}]

        self.assertEqual(self._match(tokens, "Open AI"), (1, 2))

    def test_keeps_adjacent_digits_unspaced(self):
        tokens = [
            {"text": "约"},
            {"text": "1"},
            {"text": "7"},
            {"text": "0"},
            {"text": "0"},
            {"text": "字"},
        ]

        self.assertEqual(self._match(tokens, "1700"), (1, 4))

    def test_matches_across_punctuation_boundaries(self):
        tokens = [{"text": "你好"}, {"text": "，"}, {"text": "就是"}, {"text": "。"}]

        self.assertEqual(self._match(tokens, "，就是。"), (1, 3))

    def test_returns_none_when_span_is_absent(self):
        tokens = [{"text": "这句"}, {"text": "没有"}, {"text": "目标"}]

        self.assertIsNone(self._match(tokens, "不存在"))

    def test_skips_occupied_first_occurrence(self):
        tokens = [{"text": "嗯"}, {"text": "，"}, {"text": "嗯"}]

        self.assertEqual(self._match(tokens, "嗯", occupied={0}), (2, 2))

    def test_partial_character_match_spans_token_boundary(self):
        tokens = [{"text": "怎么"}, {"text": "说呢"}, {"text": "，继续"}]

        self.assertEqual(self._match(tokens, "么说"), (0, 1))


if __name__ == "__main__":
    unittest.main()
