from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from cutpoint_lab import video_exporter
from cutpoint_lab.video_exporter import _export_segment


def _hw_encoder_module(test_case: unittest.TestCase):
    try:
        return importlib.import_module("cutpoint_lab.hw_encoder")
    except ModuleNotFoundError:
        test_case.fail("硬件编码器探测模块尚未实现")


def _build_segment_cmd(test_case: unittest.TestCase):
    function = getattr(video_exporter, "build_segment_cmd", None)
    if function is None:
        test_case.fail("单段 ffmpeg 命令构造函数尚未实现")
    return function


def _video_args_for_encoder(test_case: unittest.TestCase):
    function = getattr(video_exporter, "_video_args_for_encoder", None)
    if function is None:
        test_case.fail("编码器参数映射尚未实现")
    return function


class SegmentCommandTest(unittest.TestCase):
    def test_segment_uses_input_seek_before_input_and_output_duration_after(self) -> None:
        """输入侧 seek 保持帧精确，同时避免从文件头解码到切点。"""
        with patch("cutpoint_lab.video_exporter.subprocess.run") as run:
            _export_segment(
                "ffmpeg",
                Path("source.mp4"),
                {"start_ms": 2500, "end_ms": 5000},
                Path("segment.mp4"),
                timeout_seconds=30,
            )

        command = run.call_args.args[0]
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertLess(command.index("-i"), command.index("-t"))

    def test_build_segment_cmd_keeps_seek_before_input_and_injects_threads(self) -> None:
        command = _build_segment_cmd(self)(
            "ffmpeg",
            Path("source.mp4"),
            2500,
            5000,
            Path("segment.mp4"),
            encoder="libx264",
            video_args=[
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            ],
            threads=3,
        )

        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertLess(command.index("-i"), command.index("-t"))
        self.assertEqual(command[command.index("-ss") + 1], "2.500")
        self.assertEqual(command[command.index("-t") + 1], "2.500")
        self.assertEqual(command[command.index("-threads") + 1], "3")
        self.assertIn("libx264", command)
        self.assertIn("veryfast", command)
        self.assertIn("20", command)

    def test_videotoolbox_args_use_source_bitrate_and_rate_limits(self) -> None:
        args = _video_args_for_encoder(self)(
            "h264_videotoolbox",
            {"width": 1920, "height": 1080, "bit_rate": 12_000_000},
        )

        self.assertEqual(
            args,
            [
                "-c:v", "h264_videotoolbox",
                "-b:v", "12000000",
                "-maxrate", "18000000",
                "-bufsize", "24000000",
                "-allow_sw", "0",
                "-pix_fmt", "yuv420p",
            ],
        )


class EncoderDefaultResolutionTest(unittest.TestCase):
    """默认走 libx264；硬件编码是显式 opt-in（encoder="auto"）。"""

    def _run(self, *, encoder, env=None):
        captured = {}

        def _fake_export(*args, **_kwargs):
            captured["encoder"] = args[7]  # _export_with_hardware_fallback 的 encoder 位置参数
            return {"encoder": args[7]}

        env_patch = patch.dict("os.environ", env or {}, clear=False)
        with (
            env_patch,
            patch.object(video_exporter, "_required_tool", return_value="ffmpeg"),
            patch.object(video_exporter, "ffprobe_duration_ms", return_value=10_000),
            patch.object(video_exporter, "_probe_source_video", return_value={}),
            patch.object(video_exporter, "pick_video_encoder", return_value="h264_videotoolbox") as pick,
            patch.object(video_exporter, "_export_with_hardware_fallback", side_effect=_fake_export),
        ):
            video_exporter.export_video_plan(
                "src.mp4",
                {"ranges": [{"start_ms": 0, "end_ms": 3000}]},
                "out.mp4",
                work_dir="/tmp/pe-enc-test",
                encoder=encoder,
            )
        return captured["encoder"], pick

    def test_default_is_libx264_without_probing(self):
        chosen, pick = self._run(encoder=None)
        self.assertEqual(chosen, "libx264")
        pick.assert_not_called()

    def test_auto_opts_into_hardware_probe(self):
        chosen, pick = self._run(encoder="auto")
        self.assertEqual(chosen, "h264_videotoolbox")
        pick.assert_called_once()

    def test_env_auto_opts_in(self):
        chosen, _ = self._run(encoder=None, env={"PE_EXPORT_ENCODER": "auto"})
        self.assertEqual(chosen, "h264_videotoolbox")

    def test_explicit_encoder_name_is_honored(self):
        chosen, pick = self._run(encoder="h264_nvenc")
        self.assertEqual(chosen, "h264_nvenc")
        pick.assert_not_called()


class HardwareEncoderSelectionTest(unittest.TestCase):
    def test_darwin_uses_videotoolbox_when_probe_succeeds(self) -> None:
        module = _hw_encoder_module(self)
        with (
            patch.object(module.sys, "platform", "darwin"),
            patch.object(module, "list_ffmpeg_encoders", return_value={"h264_videotoolbox"}),
            patch.object(module, "probe_encoder", return_value=True) as probe,
        ):
            chosen = module.pick_video_encoder("ffmpeg")

        self.assertEqual(chosen, "h264_videotoolbox")
        probe.assert_called_once_with("ffmpeg", "h264_videotoolbox")

    def test_probe_failures_fall_back_to_libx264(self) -> None:
        module = _hw_encoder_module(self)
        with (
            patch.object(module.sys, "platform", "darwin"),
            patch.object(module, "list_ffmpeg_encoders", return_value={"h264_videotoolbox"}),
            patch.object(module, "probe_encoder", return_value=False),
        ):
            chosen = module.pick_video_encoder("ffmpeg")

        self.assertEqual(chosen, "libx264")

    def test_override_returns_directly_without_probing(self) -> None:
        module = _hw_encoder_module(self)
        with (
            patch.object(module, "list_ffmpeg_encoders") as encoders,
            patch.object(module, "probe_encoder") as probe,
        ):
            chosen = module.pick_video_encoder("ffmpeg", override="h264_nvenc")

        self.assertEqual(chosen, "h264_nvenc")
        encoders.assert_not_called()
        probe.assert_not_called()

    def test_non_macos_windows_platform_falls_back_to_libx264(self) -> None:
        module = _hw_encoder_module(self)
        with (
            patch.object(module.sys, "platform", "linux"),
            patch.object(module, "list_ffmpeg_encoders") as encoders,
            patch.object(module, "probe_encoder") as probe,
        ):
            chosen = module.pick_video_encoder("ffmpeg")

        self.assertEqual(chosen, "libx264")
        encoders.assert_not_called()
        probe.assert_not_called()
