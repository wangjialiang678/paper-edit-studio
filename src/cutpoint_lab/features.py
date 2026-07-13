from __future__ import annotations

import math
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioFrame:
    start_ms: int
    end_ms: int
    rms_db: float


def extract_audio(input_media: str | Path, output_wav: str | Path, timeout_seconds: int = 600) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_media),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(output_wav),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_seconds)
    validate_wav_content(output_wav)


def validate_wav_content(wav_path: str | Path, silence_floor_db: float = -90.0) -> None:
    frames = load_rms_frames(wav_path)
    if not frames:
        raise ValueError("Extracted wav contains no audio frames")
    if max(frame.rms_db for frame in frames) <= silence_floor_db:
        raise ValueError("Extracted wav appears to be silent")


def load_rms_frames(wav_path: str | Path, frame_ms: int = 20) -> list[AudioFrame]:
    with wave.open(str(wav_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        if sample_width != 2:
            raise ValueError("Expected 16-bit PCM wav")
        frame_samples = max(1, int(sample_rate * frame_ms / 1000))
        frames: list[AudioFrame] = []
        index = 0
        while True:
            raw = wav.readframes(frame_samples)
            if not raw:
                break
            samples = _decode_pcm16(raw, channels)
            rms_db = _rms_db(samples)
            start_ms = round(index * 1000 / sample_rate)
            end_ms = round((index + len(samples)) * 1000 / sample_rate)
            frames.append(AudioFrame(start_ms=start_ms, end_ms=end_ms, rms_db=rms_db))
            index += len(samples)
        return frames


def _decode_pcm16(raw: bytes, channels: int) -> list[int]:
    values = []
    step = 2 * channels
    for offset in range(0, len(raw) - step + 1, step):
        channel_values = [
            int.from_bytes(raw[offset + 2 * ch : offset + 2 * ch + 2], "little", signed=True)
            for ch in range(channels)
        ]
        values.append(round(sum(channel_values) / channels))
    return values


def _rms_db(samples: list[int]) -> float:
    if not samples:
        return -120.0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square <= 0:
        return -120.0
    rms = math.sqrt(mean_square) / 32768.0
    return 20.0 * math.log10(max(rms, 1e-9))
