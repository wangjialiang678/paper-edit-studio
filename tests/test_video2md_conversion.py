import unittest

from cutpoint_lab.dashscope import convert_dashscope_transcript
from cutpoint_lab.io import load_transcript, load_vad
from cutpoint_lab.video2md import convert_video2md_transcript


class Video2mdConversionTests(unittest.TestCase):
    def test_converts_segments_and_words_to_cutpoint_transcript(self):
        payload = {
            "schema": "video2md/transcript@1",
            "source": "sample_01.mp4",
            "task_id": "task-abc",
            "text": "你好，世界。第二句。",
            "segments": [
                {
                    "index": 1,
                    "begin_ms": 100,
                    "end_ms": 900,
                    "speaker_id": 1,
                    "text": "你好，世界。",
                    "words": [
                        {"begin_ms": 120, "end_ms": 300, "text": "你好", "punctuation": "，", "confidence": 0.98},
                        {"begin_ms": 420, "end_ms": 780, "text": "世界", "punctuation": "。", "confidence": 0.95},
                    ],
                },
                {
                    "index": 2,
                    "begin_ms": 1200,
                    "end_ms": 1800,
                    "speaker_id": 2,
                    "text": "第二句。",
                    "words": [
                        {"begin_ms": 1240, "end_ms": 1700, "text": "第二句", "punctuation": "。", "confidence": 0.9},
                    ],
                },
            ],
        }

        converted = convert_video2md_transcript(payload, source_video="sample_01.mp4")
        transcript = load_transcript(converted["transcript"])

        self.assertEqual(transcript.source_video, "sample_01.mp4")
        # 无媒体时长字段 → 用最大 end_ms 作内容时长代理。
        self.assertEqual(transcript.duration_ms, 1800)
        self.assertEqual(transcript.selected_segment_ids, ["sentence_0001", "sentence_0002"])
        self.assertEqual(len(transcript.segments), 2)
        self.assertEqual(transcript.segments[0].text, "你好，世界。")
        self.assertEqual([token.text for token in transcript.segments[0].tokens], ["你好，", "世界。"])
        self.assertEqual(
            (transcript.segments[0].tokens[0].start_ms, transcript.segments[0].tokens[0].end_ms),
            (120, 300),
        )

    def test_builds_vad_proxy_with_confidence_and_skips_bad_words(self):
        payload = {
            "schema": "video2md/transcript@1",
            "segments": [
                {
                    "index": 1,
                    "begin_ms": 100,
                    "end_ms": 900,
                    "text": "测试。",
                    "words": [
                        {"begin_ms": 100, "end_ms": 260, "text": "测", "confidence": 0.9},
                        {"begin_ms": 360, "end_ms": 520, "text": "试", "confidence": 0.7},
                        {"begin_ms": -1, "end_ms": 700, "text": "坏"},
                    ],
                }
            ],
        }

        converted = convert_video2md_transcript(payload)
        vad = load_vad(converted["vad"])

        self.assertEqual(
            [(item.start_ms, item.end_ms) for item in vad.normalized_speech(merge_gap_ms=0)],
            [(100, 260), (360, 520)],
        )
        # video2md 带真实置信度（DashScope 代理是 None）。
        self.assertEqual(converted["vad"]["speech_intervals"][0]["confidence"], 0.9)
        self.assertEqual(converted["vad"]["source"], "video2md_word_timestamps_proxy")

    def test_output_shape_matches_dashscope_converter(self):
        # 两个转换器必须产出同构的内部 schema，才能在流水线里互换。
        video2md_payload = {
            "schema": "video2md/transcript@1",
            "segments": [
                {
                    "index": 1,
                    "begin_ms": 100,
                    "end_ms": 900,
                    "text": "你好世界",
                    "words": [
                        {"begin_ms": 120, "end_ms": 300, "text": "你好"},
                        {"begin_ms": 420, "end_ms": 780, "text": "世界"},
                    ],
                }
            ],
        }
        dashscope_payload = {
            "transcripts": [
                {
                    "sentences": [
                        {
                            "sentence_id": 1,
                            "begin_time": 100,
                            "end_time": 900,
                            "text": "你好世界",
                            "words": [
                                {"begin_time": 120, "end_time": 300, "text": "你好"},
                                {"begin_time": 420, "end_time": 780, "text": "世界"},
                            ],
                        }
                    ],
                }
            ],
        }

        a = convert_video2md_transcript(video2md_payload)
        b = convert_dashscope_transcript(dashscope_payload)

        self.assertEqual(set(a.keys()), set(b.keys()))
        self.assertEqual(set(a["transcript"].keys()), set(b["transcript"].keys()))
        self.assertEqual(set(a["vad"].keys()), set(b["vad"].keys()))
        # segment / token 时间戳应逐一对齐（同一句同样的词级时间）。
        self.assertEqual(a["transcript"]["segments"], b["transcript"]["segments"])
        self.assertEqual(
            [(i["start_ms"], i["end_ms"]) for i in a["vad"]["speech_intervals"]],
            [(i["start_ms"], i["end_ms"]) for i in b["vad"]["speech_intervals"]],
        )

    def test_rejects_payload_without_segments(self):
        with self.assertRaises(ValueError):
            convert_video2md_transcript({"schema": "video2md/transcript@1", "segments": []})

    def test_rejects_unexpected_schema(self):
        with self.assertRaises(ValueError):
            convert_video2md_transcript({"schema": "something/else@9", "segments": [{"index": 1, "text": "x"}]})


if __name__ == "__main__":
    unittest.main()
