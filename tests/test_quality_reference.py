from __future__ import annotations

import unittest

from cutpoint_lab.quality.align_reference import align, parse_reference


class ReferenceParserTests(unittest.TestCase):
    def test_srt_tolerates_bom_crlf_missing_indexes_and_multiline_text(self):
        cues = parse_reference(
            "\ufeff00:00:00,000 --> 00:00:01,000\r\n"
            "第一行\r\n第二行\r\n\r\n"
            "17\r\n00:00:01.200 --> 00:00:02.300\r\n下一句\r\n"
        )

        self.assertEqual(
            cues,
            [
                {"start_ms": 0, "end_ms": 1000, "text": "第一行\n第二行"},
                {"start_ms": 1200, "end_ms": 2300, "text": "下一句"},
            ],
        )

    def test_vtt_skips_header_note_and_accepts_cue_identifier_and_settings(self):
        cues = parse_reference(
            "WEBVTT\n\n"
            "NOTE 这是一段说明\n不会成为字幕\n\n"
            "cue-a\n"
            "00:00.000 --> 00:01.500 align:start position:0%\n"
            "Hello\nWorld\n\n"
            "00:02.000 --> 00:03.000\n"
            "Done\n"
        )

        self.assertEqual(
            cues,
            [
                {"start_ms": 0, "end_ms": 1500, "text": "Hello\nWorld"},
                {"start_ms": 2000, "end_ms": 3000, "text": "Done"},
            ],
        )


class ReferenceAlignmentTests(unittest.TestCase):
    def test_partial_overlap_uses_corresponding_time_weighted_text(self):
        segments = [
            {
                "id": "s1",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "你好",
            }
        ]
        cues = [{"start_ms": 0, "end_ms": 2000, "text": "你好世界"}]

        self.assertEqual(align(segments, cues), [])

    def test_punctuation_only_replacements_are_ignored(self):
        segments = [
            {
                "id": "s1",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "你好，世界！",
            }
        ]
        cues = [{"start_ms": 0, "end_ms": 1000, "text": "你好。世界？"}]

        self.assertEqual(align(segments, cues), [])

    def test_high_ratio_reports_beijing_as_short_replacement(self):
        issues = align(
            [
                {
                    "id": "s1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "我今天从北京出发去参加一个重要活动",
                }
            ],
            [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "我今天从南京出发去参加一个重要活动",
                }
            ],
        )

        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["span"], {"text": "北"})
        self.assertEqual(issue["suggestion"], "南")
        self.assertGreaterEqual(issue["confidence"], 0.85)
        self.assertIn("参考字幕此处为「南」", issue["reason"])

    def test_high_ratio_reports_person_name_replacement(self):
        issues = align(
            [
                {
                    "id": "s1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "今天采访宋海峰老师聊人工智能",
                }
            ],
            [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "今天采访宋海丰老师聊人工智能",
                }
            ],
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["span"], {"text": "峰"})
        self.assertEqual(issues[0]["suggestion"], "丰")

    def test_high_ratio_reports_case_and_word_splitting_replacement(self):
        issues = align(
            [
                {
                    "id": "s1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "这是一个a i工具，挺好用的",
                }
            ],
            [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "这是一个AI工具，挺好用的",
                }
            ],
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["span"], {"text": "a i"})
        self.assertEqual(issues[0]["suggestion"], "AI")
        self.assertEqual(issues[0]["confidence"], 1.0)

    def test_low_ratio_creates_reference_issue_with_original_suggestion(self):
        issues = align(
            [{"id": "s1", "start_ms": 0, "end_ms": 2000, "text": "完全不同"}],
            [
                {"start_ms": 0, "end_ms": 1000, "text": "第一行"},
                {"start_ms": 1000, "end_ms": 2000, "text": "第二行"},
            ],
        )

        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["kind"], "ref_mismatch")
        self.assertEqual(issue["source"], "reference")
        self.assertEqual(issue["span"], {"text": "完全不同"})
        self.assertEqual(issue["suggestion"], "第一行\n第二行")
        self.assertLess(issue["confidence"], 0.85)
        self.assertIn("相似度", issue["reason"])

    def test_high_ratio_ignores_replacement_longer_than_eight_characters(self):
        prefix = "这是前面完全一致的内容用于确保整句相似度足够高并且不会产生其他替换"
        suffix = "这是后面同样完全一致的内容继续延长句子确保整体仍然高度相似"

        issues = align(
            [
                {
                    "id": "s1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": prefix + "甲乙丙丁戊己庚辛壬" + suffix,
                }
            ],
            [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": prefix + "子丑寅卯辰巳午未申" + suffix,
                }
            ],
        )

        self.assertEqual(issues, [])

    def test_unique_token_concatenation_match_adds_inclusive_token_indexes(self):
        issues = align(
            [
                {
                    "id": "s1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "我在北京工作很开心",
                    "tokens": [
                        {"text": "我在"},
                        {"text": "北京"},
                        {"text": "工作"},
                        {"text": "很开心"},
                    ],
                }
            ],
            [{"start_ms": 0, "end_ms": 1000, "text": "我在南京工作很开心"}],
        )

        self.assertEqual(
            issues[0]["span"],
            {"text": "北", "token_start": 1, "token_end": 1},
        )

    def test_non_unique_token_concatenation_match_omits_token_indexes(self):
        issues = align(
            [
                {
                    "id": "s1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "北京人在北京工作很开心",
                    "tokens": [
                        {"text": "北京人"},
                        {"text": "在"},
                        {"text": "北京"},
                        {"text": "工作"},
                        {"text": "很开心"},
                    ],
                }
            ],
            [{"start_ms": 0, "end_ms": 1000, "text": "南京人在北京工作很开心"}],
        )

        self.assertEqual(issues[0]["span"], {"text": "北"})

    def test_no_overlap_and_empty_reference_are_skipped(self):
        segments = [{"id": "s1", "start_ms": 0, "end_ms": 1000, "text": "正文"}]

        self.assertEqual(
            align(segments, [{"start_ms": 1000, "end_ms": 2000, "text": "边界"}]),
            [],
        )
        self.assertEqual(
            align(segments, [{"start_ms": 0, "end_ms": 1000, "text": " ，！ "}]),
            [],
        )


if __name__ == "__main__":
    unittest.main()
