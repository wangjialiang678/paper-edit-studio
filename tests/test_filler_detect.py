import unittest

from cutpoint_lab.studio.filler_detect import detect


def _tokens(*texts: str) -> list[dict[str, int | str]]:
    return [
        {"text": text, "start_ms": index * 100, "end_ms": index * 100 + 80}
        for index, text in enumerate(texts)
    ]


class FillerDetectTests(unittest.TestCase):
    def test_detects_punctuation_wrapped_filler_tokens(self):
        result = detect(_tokens("我", "，嗯。", "知道"))

        self.assertEqual(
            result,
            [{"start_token": 1, "end_token": 1, "kind": "filler", "text": "，嗯。"}],
        )

    def test_detects_adjacent_repeats_from_one_to_four_grams(self):
        cases = [
            (("对", "对", "了"), (0, 0)),
            (("我", "觉得", "我", "觉得", "可以"), (0, 1)),
            (("这", "是", "一个", "这", "是", "一个", "观点"), (0, 2)),
            (("a", "b", "c", "d", "a", "b", "c", "d", "e"), (0, 3)),
        ]
        for texts, expected in cases:
            with self.subTest(texts=texts):
                result = detect(_tokens(*texts))
                self.assertEqual(
                    [(item["start_token"], item["end_token"], item["kind"]) for item in result],
                    [(expected[0], expected[1], "repeat")],
                )

    def test_triple_repeat_removes_every_occurrence_except_the_last(self):
        result = detect(_tokens("然后", "然后", "然后", "继续"))

        self.assertEqual(
            result,
            [{"start_token": 0, "end_token": 1, "kind": "repeat", "text": "然后然后"}],
        )

    def test_repeat_span_includes_punctuation_only_tokens_before_kept_copy(self):
        result = detect(_tokens("我", "觉得", "，", "我", "觉得", "可以"))

        self.assertEqual(
            result,
            [{"start_token": 0, "end_token": 2, "kind": "repeat", "text": "我觉得，"}],
        )

    def test_overlapping_or_adjacent_suggestions_are_merged(self):
        result = detect(_tokens("嗯", "我", "我", "知道"))

        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["start_token"], result[0]["end_token"]), (0, 1))
        self.assertIn(result[0]["kind"], {"filler", "repeat"})
        self.assertEqual(result[0]["text"], "嗯我")

    def test_digit_repeats_are_not_flagged(self):
        # "800" 被 ASR 拆成 8/0/0，连续的 "0" 不是气口；纯数字 n-gram 同理。
        self.assertEqual(detect(_tokens("将近", "8", "0", "0", "多所", "中学")), [])
        self.assertEqual(detect(_tokens("编号", "17", "17", "结束")), [])

    def test_legitimate_reduplication_words_are_not_flagged(self):
        # 谢谢/慢慢等合法叠词按单字 token 出现时不能当口吃剪掉。
        self.assertEqual(detect(_tokens("谢", "谢", "大家")), [])
        self.assertEqual(detect(_tokens("我们", "慢", "慢", "来")), [])

    def test_stutter_single_char_whitelist_still_detected(self):
        result = detect(_tokens("我", "我", "知道"))
        self.assertEqual(
            [(item["start_token"], item["end_token"], item["kind"]) for item in result],
            [(0, 0, "repeat")],
        )

    def test_returns_empty_when_suggestions_would_cover_every_token(self):
        self.assertEqual(detect(_tokens("嗯", "嗯")), [])
        self.assertEqual(detect(_tokens()), [])

    def test_full_coverage_guard_uses_raw_token_indexes(self):
        result = detect(_tokens("嗯", "，"))

        self.assertEqual(
            result,
            [{"start_token": 0, "end_token": 0, "kind": "filler", "text": "嗯"}],
        )


if __name__ == "__main__":
    unittest.main()
