"""把 video2md（mp4-md --emit-json）产出的结构化转写 JSON 转成内部 Transcript schema。

与 `dashscope.convert_dashscope_transcript` 输出同一份 `{transcript, vad}` 结构，
供剪辑流水线消费，二者可互换。差异仅在输入来源：这里是 video2md 的
`video2md/transcript@1` 文档（见 video2md-app/internal/transcriptjson）。
"""

from __future__ import annotations

from typing import Any

SCHEMA_PREFIX = "video2md/transcript"


def convert_video2md_transcript(data: dict[str, Any], source_video: str | None = None) -> dict[str, dict[str, Any]]:
    schema = str(data.get("schema") or "")
    if schema and not schema.startswith(SCHEMA_PREFIX):
        raise ValueError(f"unexpected video2md transcript schema: {schema!r}")

    segments_in = data.get("segments") or []
    if not segments_in:
        raise ValueError("video2md transcript contains no segments")

    segments = []
    selected_segment_ids = []
    speech_intervals = []
    max_end_ms = 0

    for index, segment in enumerate(segments_in, start=1):
        segment_id = _segment_id(segment, index)
        tokens = []
        for word in segment.get("words") or []:
            token = _word_token(word)
            if token is None:
                continue
            token_payload = {
                "text": token["text"],
                "start_ms": token["start_ms"],
                "end_ms": token["end_ms"],
            }
            if token["confidence"] is not None:
                token_payload["confidence"] = token["confidence"]
            tokens.append(token_payload)
            speech_intervals.append(
                {
                    "start_ms": token["start_ms"],
                    "end_ms": token["end_ms"],
                    "confidence": token["confidence"],
                }
            )
            max_end_ms = max(max_end_ms, token["end_ms"])

        start_ms = _int_ms(segment.get("begin_ms"))
        end_ms = _int_ms(segment.get("end_ms"))
        max_end_ms = max(max_end_ms, end_ms)
        segments.append(
            {
                "id": segment_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": str(segment.get("text") or ""),
                "tokens": tokens,
            }
        )
        selected_segment_ids.append(segment_id)

    # video2md 的 Transcript 不携带媒体总时长；用最大 end_ms 作为内容时长代理，
    # 用于 VAD 尾部空隙检测。导出与钳制用的真实时长另由 ffprobe 提供。
    duration_ms = max_end_ms or None

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
            "source": "video2md_word_timestamps_proxy",
        },
    }


def _segment_id(segment: dict[str, Any], index: int) -> str:
    raw = segment.get("index")
    if raw is None:
        return f"sentence_{index:04d}"
    try:
        return f"sentence_{int(raw):04d}"
    except (TypeError, ValueError):
        return f"sentence_{index:04d}"


def _word_token(word: dict[str, Any]) -> dict[str, Any] | None:
    start_ms = _int_ms(word.get("begin_ms"))
    end_ms = _int_ms(word.get("end_ms"))
    text = str(word.get("text") or "")
    punctuation = str(word.get("punctuation") or "")
    if start_ms < 0 or end_ms <= start_ms or not text.strip():
        return None
    confidence = word.get("confidence")
    return {
        "text": text + punctuation,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "confidence": float(confidence) if confidence is not None else None,
    }


def _int_ms(value: Any) -> int:
    if value is None:
        return 0
    return int(round(float(value)))
