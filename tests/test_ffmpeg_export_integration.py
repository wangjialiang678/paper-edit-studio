"""
Integration test: full ffmpeg export pipeline.

Generates a synthetic source video (testsrc+sine, no real media files),
runs export_video_plan with a fixed clip plan, then asserts:
  1. Output duration matches the clip plan (frame-level tolerance).
  2. Output contains both video and audio streams.
  3. SRT subtitle timestamps are monotonically non-decreasing and
     fall within the output duration.

All assertions are offline — no API keys or network access required.
Skipped automatically when ffmpeg/ffprobe are not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.models import Transcript, TranscriptSegment
from cutpoint_lab.subtitle_exporter import write_srt
from cutpoint_lab.video_exporter import export_video_plan


def _tools_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_test_video(path: Path, duration_s: int = 10) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration_s}:size=320x240:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


@unittest.skipUnless(_tools_available(), "ffmpeg/ffprobe not installed — skipping integration test")
class FfmpegExportIntegrationTest(unittest.TestCase):
    """End-to-end test: synthetic source → export_video_plan → ffprobe assertions."""

    _tmpdir: tempfile.TemporaryDirectory
    _source: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmpdir.name)
        cls._source = tmp / "source.mp4"
        _make_test_video(cls._source, duration_s=10)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def test_full_export_pipeline(self) -> None:
        """
        Fixed clip plan: keep 0–3 s and 5–8 s (expected output ≈ 6 000 ms).
        Asserts duration, stream presence, and SRT cue ordering.
        """
        tmp = Path(self._tmpdir.name)
        clip_plan: dict = {
            "ranges": [
                {"start_ms": 0, "end_ms": 3000},
                {"start_ms": 5000, "end_ms": 8000},
            ]
        }
        output_video = tmp / "edited.mp4"
        result = export_video_plan(self._source, clip_plan, output_video)

        expected_ms = 6000
        tolerance_ms = 200  # ≈ 5 frames at 25 fps

        # ① Duration matches clip plan
        self.assertAlmostEqual(
            result["duration_ms"],
            expected_ms,
            delta=tolerance_ms,
            msg=f"Output duration {result['duration_ms']} ms deviates from expected {expected_ms} ms",
        )

        # ② Output contains both video and audio streams
        probe_result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_streams", "-of", "json",
                str(output_video),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        streams = json.loads(probe_result.stdout).get("streams", [])
        codec_types = {s.get("codec_type") for s in streams}
        self.assertIn("video", codec_types, "Output must contain a video stream")
        self.assertIn("audio", codec_types, "Output must contain an audio stream")

        # ③ SRT subtitle timestamps are monotonically non-decreasing and
        #    all cue end times fall within the output duration
        transcript = Transcript(
            source_video=str(self._source),
            duration_ms=10000,
            selected_segment_ids=["seg_a", "seg_b"],
            segments=[
                TranscriptSegment(id="seg_a", start_ms=500, end_ms=2500, text="First clip"),
                TranscriptSegment(id="seg_b", start_ms=5500, end_ms=7500, text="Second clip"),
            ],
        )
        srt_path = tmp / "edited.srt"
        cues = write_srt(transcript, clip_plan, srt_path)

        self.assertGreater(len(cues), 0, "Expected at least one subtitle cue")

        for i in range(1, len(cues)):
            self.assertGreaterEqual(
                cues[i].start_ms,
                cues[i - 1].start_ms,
                f"Cue {i} start_ms={cues[i].start_ms} < cue {i-1} start_ms={cues[i-1].start_ms}: not monotonic",
            )

        output_duration_ms = result["duration_ms"]
        for cue in cues:
            self.assertLessEqual(
                cue.end_ms,
                output_duration_ms + tolerance_ms,
                f"Cue end_ms={cue.end_ms} exceeds output duration={output_duration_ms} ms",
            )
