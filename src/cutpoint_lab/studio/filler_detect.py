from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any


FILLER_TOKENS = {"呃", "嗯", "唉", "嗯嗯", "呃呃"}
MAX_REPEAT_NGRAM = 4

# 单字重复只在这些口吃高发的虚词/代词上生效；
# 其余单字重复多为合法叠词（谢谢/慢慢/天天）或数字（"800" 拆成 8/0/0），不能当气口剪。
STUTTER_SINGLE_CHARS = {"我", "你", "他", "她", "它", "就", "这", "那", "对", "是", "要", "在", "很", "都", "和", "但"}


def detect(tokens: Sequence[Any]) -> list[dict[str, int | str]]:
    """按输入 token 原始索引返回语气词与紧邻重复建议。"""
    texts = [_token_text(token) for token in tokens]
    normalized = [
        (index, cleaned)
        for index, text in enumerate(texts)
        if (cleaned := _strip_boundary_punctuation(text))
    ]
    if not normalized:
        return []

    suggestions = [
        {"start_token": index, "end_token": index, "kind": "filler"}
        for index, text in normalized
        if text in FILLER_TOKENS
    ]
    suggestions.extend(_repeat_spans(normalized))

    merged = _merge_spans(suggestions)
    if _covers_all_tokens(merged, len(texts)):
        return []
    return [
        {
            **span,
            "text": _join_tokens(texts[span["start_token"] : span["end_token"] + 1]),
        }
        for span in merged
    ]


def _token_text(token: Any) -> str:
    if isinstance(token, dict):
        value = token.get("text", "")
    elif isinstance(token, str):
        value = token
    else:
        value = getattr(token, "text", "")
    return "" if value is None else str(value)


def _strip_boundary_punctuation(text: str) -> str:
    start = 0
    end = len(text)
    while start < end and _is_boundary_character(text[start]):
        start += 1
    while end > start and _is_boundary_character(text[end - 1]):
        end -= 1
    return text[start:end]


def _is_boundary_character(character: str) -> bool:
    return character.isspace() or unicodedata.category(character).startswith("P")


def _repeat_spans(normalized: list[tuple[int, str]]) -> list[dict[str, int | str]]:
    words = [text for _, text in normalized]
    spans: list[dict[str, int | str]] = []
    for width in range(1, min(MAX_REPEAT_NGRAM, len(words) // 2) + 1):
        index = 0
        while index + 2 * width <= len(words):
            phrase = words[index : index + width]
            if phrase != words[index + width : index + 2 * width] or not _is_cuttable_repeat_unit(phrase):
                index += 1
                continue

            copies = 2
            while words[index + copies * width : index + (copies + 1) * width] == phrase:
                copies += 1
            kept_start = index + (copies - 1) * width
            spans.append(
                {
                    "start_token": normalized[index][0],
                    "end_token": normalized[kept_start][0] - 1,
                    "kind": "repeat",
                }
            )
            index += copies * width
    return spans


def _is_cuttable_repeat_unit(phrase: list[str]) -> bool:
    """数字重复（"800"→8/0/0）与合法叠词不是气口，只放行真正的口吃型重复。"""
    unit = "".join(phrase)
    if not unit or unit.isdigit():
        return False
    if len(unit) == 1:
        return unit in STUTTER_SINGLE_CHARS
    return True


def _merge_spans(spans: list[dict[str, int | str]]) -> list[dict[str, int | str]]:
    ordered = sorted(
        spans,
        key=lambda span: (
            int(span["start_token"]),
            int(span["end_token"]),
            0 if span["kind"] == "filler" else 1,
        ),
    )
    merged: list[dict[str, int | str]] = []
    for span in ordered:
        start = int(span["start_token"])
        end = int(span["end_token"])
        if merged and start <= int(merged[-1]["end_token"]) + 1:
            merged[-1]["end_token"] = max(int(merged[-1]["end_token"]), end)
            continue
        merged.append({"start_token": start, "end_token": end, "kind": str(span["kind"])})
    return merged


def _covers_all_tokens(
    spans: list[dict[str, int | str]],
    token_count: int,
) -> bool:
    return bool(spans) and all(
        any(int(span["start_token"]) <= index <= int(span["end_token"]) for span in spans)
        for index in range(token_count)
    )


def _join_tokens(texts: Sequence[str]) -> str:
    output = ""
    for text in texts:
        if output and _is_ascii_alnum(output[-1]) and text and _is_ascii_alnum(text[0]):
            output += " "
        output += text
    return output


def _is_ascii_alnum(character: str) -> bool:
    # 仅字母之间补空格：数字序列（"1"/"7"/"0"/"0"）须连写还原成 "1700"。
    return "A" <= character <= "Z" or "a" <= character <= "z"
