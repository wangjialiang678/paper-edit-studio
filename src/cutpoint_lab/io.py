from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import SpeakerData, Transcript, TranscriptSegment, TranscriptToken, VadData


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_transcript(data_or_path: dict[str, Any] | str | Path) -> Transcript:
    data = read_json(data_or_path) if isinstance(data_or_path, str | Path) else data_or_path
    if "selected_segment_ids" not in data:
        raise ValueError("Transcript JSON must include selected_segment_ids")
    segments = []
    for item in data.get("segments", []):
        tokens = [
            TranscriptToken(
                text=str(token.get("text", "")),
                start_ms=int(token.get("start_ms", 0)),
                end_ms=int(token.get("end_ms", 0)),
                confidence=float(token["confidence"]) if token.get("confidence") is not None else None,
            )
            for token in item.get("tokens", [])
        ]
        segments.append(
            TranscriptSegment(
                id=str(item["id"]),
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                text=str(item.get("text", "")),
                tokens=tokens,
            )
        )
    return Transcript(
        source_video=data.get("source_video"),
        duration_ms=int(data["duration_ms"]) if data.get("duration_ms") is not None else None,
        selected_segment_ids=[str(item) for item in data.get("selected_segment_ids", [])],
        segments=segments,
    )


def load_vad(data_or_path: dict[str, Any] | str | Path | None) -> VadData | None:
    if data_or_path is None:
        return None
    data = read_json(data_or_path) if isinstance(data_or_path, str | Path) else data_or_path
    return VadData(
        duration_ms=int(data["duration_ms"]) if data.get("duration_ms") is not None else None,
        speech_intervals=list(data.get("speech_intervals", [])),
    )


def load_speaker_data(data_or_path: dict[str, Any] | str | Path | None) -> SpeakerData | None:
    if data_or_path is None:
        return None
    data = read_json(data_or_path) if isinstance(data_or_path, str | Path) else data_or_path
    return SpeakerData(
        duration_ms=int(data["duration_ms"]) if data.get("duration_ms") is not None else None,
        speaker_segments=list(data.get("speaker_segments", [])),
        overlap_segments=list(data.get("overlap_segments", [])),
    )
