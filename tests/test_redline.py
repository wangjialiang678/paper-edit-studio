import unittest

from cutpoint_lab.models import Transcript, TranscriptSegment, TranscriptToken
from cutpoint_lab.paper_edit.redline import render_redline_markdown


def _transcript() -> Transcript:
    segments = [
        TranscriptSegment(
            id="sentence_0001",
            start_ms=0,
            end_ms=900,
            text="先说核心结论。",
            tokens=[TranscriptToken(text="先说核心结论。", start_ms=40, end_ms=860)],
        ),
        TranscriptSegment(
            id="sentence_0002",
            start_ms=1000,
            end_ms=1800,
            text="这句话重复了前面的意思。",
            tokens=[TranscriptToken(text="这句话重复了前面的意思。", start_ms=1040, end_ms=1760)],
        ),
        TranscriptSegment(
            id="sentence_0003",
            start_ms=1900,
            end_ms=2900,
            text="接着给出具体做法。",
            tokens=[TranscriptToken(text="接着给出具体做法。", start_ms=1940, end_ms=2860)],
        ),
        TranscriptSegment(
            id="sentence_0004",
            start_ms=3000,
            end_ms=3500,
            text="嗯，就这样。",
            tokens=[TranscriptToken(text="嗯，就这样。", start_ms=3040, end_ms=3460)],
        ),
    ]
    return Transcript(
        source_video="source.mp4",
        duration_ms=3500,
        selected_segment_ids=[segment.id for segment in segments],
        segments=segments,
    )


class RenderRedlineMarkdownTests(unittest.TestCase):
    def test_marks_deleted_sentences_with_inline_reasons_and_exact_counts(self):
        markdown = render_redline_markdown(
            _transcript(),
            keeps={"sentence_0001", "sentence_0003"},
            decisions={
                "sentence_0001": {"keep": True, "reason": "开门见山"},
                "sentence_0002": {"keep": False, "reason": "与上一句重复"},
                "sentence_0003": {"keep": True, "reason": "保留方法"},
                "sentence_0004": {"keep": False, "reason": "口头收尾"},
            },
            title="口播精剪修订稿",
        )

        self.assertIn("# 口播精剪修订稿", markdown)
        self.assertIn("- 原始 4 句 / 保留 2 句 / 删除 2 句", markdown)

        lines = markdown.splitlines()
        kept_lines = [line for line in lines if "先说核心结论。" in line or "接着给出具体做法。" in line]
        self.assertEqual(len(kept_lines), 2)
        self.assertTrue(all("~~" not in line for line in kept_lines))

        repeated_line = next(line for line in lines if "这句话重复了前面的意思。" in line)
        filler_line = next(line for line in lines if "嗯，就这样。" in line)
        self.assertTrue(repeated_line.endswith("~~这句话重复了前面的意思。~~ · 与上一句重复"))
        self.assertTrue(filler_line.endswith("~~嗯，就这样。~~ · 口头收尾"))


if __name__ == "__main__":
    unittest.main()
