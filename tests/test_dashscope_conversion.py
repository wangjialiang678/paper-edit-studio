import unittest

from cutpoint_lab.dashscope import convert_dashscope_transcript
from cutpoint_lab.io import load_transcript, load_vad


class DashScopeConversionTests(unittest.TestCase):
    def test_converts_sentences_and_words_to_cutpoint_transcript(self):
        payload = {
            "properties": {"original_duration_in_milliseconds": 2500},
            "transcripts": [
                {
                    "content_duration_in_milliseconds": 2400,
                    "sentences": [
                        {
                            "sentence_id": 1,
                            "begin_time": 100,
                            "end_time": 900,
                            "text": "你好，世界。",
                            "words": [
                                {"begin_time": 120, "end_time": 300, "text": "你好", "punctuation": "，"},
                                {"begin_time": 420, "end_time": 780, "text": "世界", "punctuation": "。"},
                            ],
                        },
                        {
                            "sentence_id": 2,
                            "begin_time": 1200,
                            "end_time": 1800,
                            "text": "第二句。",
                            "words": [
                                {"begin_time": 1240, "end_time": 1700, "text": "第二句", "punctuation": "。"},
                            ],
                        },
                    ],
                }
            ],
        }

        converted = convert_dashscope_transcript(payload, source_video="sample_01_audio.m4a")
        transcript = load_transcript(converted["transcript"])

        self.assertEqual(transcript.source_video, "sample_01_audio.m4a")
        self.assertEqual(transcript.duration_ms, 2500)
        self.assertEqual(transcript.selected_segment_ids, ["sentence_0001", "sentence_0002"])
        self.assertEqual(len(transcript.segments), 2)
        self.assertEqual(transcript.segments[0].text, "你好，世界。")
        self.assertEqual([token.text for token in transcript.segments[0].tokens], ["你好，", "世界。"])
        self.assertEqual((transcript.segments[0].tokens[0].start_ms, transcript.segments[0].tokens[0].end_ms), (120, 300))

    def test_builds_vad_proxy_from_word_timestamps(self):
        payload = {
            "properties": {"original_duration_in_milliseconds": 2000},
            "transcripts": [
                {
                    "sentences": [
                        {
                            "sentence_id": 1,
                            "begin_time": 100,
                            "end_time": 900,
                            "text": "测试。",
                            "words": [
                                {"begin_time": 100, "end_time": 260, "text": "测"},
                                {"begin_time": 360, "end_time": 520, "text": "试"},
                                {"begin_time": -1, "end_time": 700, "text": "坏"},
                            ],
                        }
                    ],
                }
            ],
        }

        converted = convert_dashscope_transcript(payload)
        vad = load_vad(converted["vad"])

        self.assertEqual(vad.duration_ms, 2000)
        self.assertEqual(
            [(item.start_ms, item.end_ms) for item in vad.normalized_speech(merge_gap_ms=0)],
            [(100, 260), (360, 520)],
        )

    def test_rejects_dashscope_payload_without_sentences(self):
        with self.assertRaises(ValueError):
            convert_dashscope_transcript({"transcripts": [{"sentences": []}]})


if __name__ == "__main__":
    unittest.main()
