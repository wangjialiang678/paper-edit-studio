from __future__ import annotations

from typing import Any


def convert_dashscope_transcript(data: dict[str, Any], source_video: str | None = None) -> dict[str, dict[str, Any]]:
    transcript = _first_transcript(data)
    sentences = transcript.get("sentences") or []
    if not sentences:
        raise ValueError("DashScope transcript contains no sentences")

    duration_ms = _duration_ms(data, transcript)
    segments = []
    selected_segment_ids = []
    speech_intervals = []

    for index, sentence in enumerate(sentences, start=1):
        segment_id = _sentence_id(sentence, index)
        tokens = []
        for word in sentence.get("words") or []:
            token = _word_token(word)
            if token is None:
                continue
            tokens.append(token)
            speech_intervals.append(
                {
                    "start_ms": token["start_ms"],
                    "end_ms": token["end_ms"],
                    "confidence": None,
                }
            )
        segments.append(
            {
                "id": segment_id,
                "start_ms": _int_ms(sentence.get("begin_time")),
                "end_ms": _int_ms(sentence.get("end_time")),
                "text": str(sentence.get("text") or ""),
                "tokens": tokens,
            }
        )
        selected_segment_ids.append(segment_id)

    return {
        "transcript": {
            "source_video": source_video,
            "duration_ms": duration_ms,
            "selected_segment_ids": selected_segment_ids,
            "segments": segments,
        },
        "vad": {
            "duration_ms": duration_ms,
            "speech_intervals": speech_intervals,
            "source": "dashscope_word_timestamps_proxy",
        },
    }


def _first_transcript(data: dict[str, Any]) -> dict[str, Any]:
    transcripts = data.get("transcripts") or []
    if not transcripts:
        raise ValueError("DashScope transcript contains no transcripts")
    first = transcripts[0]
    if not isinstance(first, dict):
        raise ValueError("DashScope transcript[0] is not an object")
    return first


def _duration_ms(data: dict[str, Any], transcript: dict[str, Any]) -> int | None:
    properties = data.get("properties") if isinstance(data.get("properties"), dict) else {}
    for value in [
        properties.get("original_duration_in_milliseconds"),
        transcript.get("content_duration_in_milliseconds"),
    ]:
        if value is not None:
            return _int_ms(value)
    return None


def _sentence_id(sentence: dict[str, Any], index: int) -> str:
    raw = sentence.get("sentence_id")
    if raw is None:
        return f"sentence_{index:04d}"
    try:
        return f"sentence_{int(raw):04d}"
    except (TypeError, ValueError):
        return f"sentence_{index:04d}"


def _word_token(word: dict[str, Any]) -> dict[str, Any] | None:
    start_ms = _int_ms(word.get("begin_time"))
    end_ms = _int_ms(word.get("end_time"))
    text = str(word.get("text") or "")
    punctuation = str(word.get("punctuation") or "")
    if start_ms < 0 or end_ms <= start_ms or not text.strip():
        return None
    return {
        "text": text + punctuation,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def _int_ms(value: Any) -> int:
    if value is None:
        return 0
    return int(round(float(value)))
