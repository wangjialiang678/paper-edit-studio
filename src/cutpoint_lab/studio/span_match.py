from __future__ import annotations

from collections.abc import Sequence
from typing import AbstractSet, Any


def join_tokens(tokens: Sequence[Any]) -> str:
    """按编辑器 valid_tokens 约定拼回句子文本。"""
    output = ""
    for token in tokens:
        text = _token_text(token)
        if (
            output
            and _is_ascii_letter(output[-1])
            and text
            and _is_ascii_letter(text[0])
        ):
            output += " "
        output += text
    return output


def match_span(
    tokens: Sequence[Any],
    span_text: str,
    occupied: AbstractSet[int] | None = None,
) -> tuple[int, int] | None:
    """把逐字 span 映射为首个未占用的 valid_tokens 闭区间。"""
    if not isinstance(span_text, str) or not span_text:
        return None

    joined, token_ranges = _joined_text_and_ranges(tokens)
    occupied_tokens = occupied or set()
    search_from = 0
    while True:
        span_start = joined.find(span_text, search_from)
        if span_start < 0:
            return None
        span_end = span_start + len(span_text)
        covered = [
            index
            for index, (token_start, token_end) in enumerate(token_ranges)
            if token_start < span_end and token_end > span_start
        ]
        if covered and not any(index in occupied_tokens for index in covered):
            return covered[0], covered[-1]
        search_from = span_start + 1


def _joined_text_and_ranges(
    tokens: Sequence[Any],
) -> tuple[str, list[tuple[int, int]]]:
    output = ""
    ranges: list[tuple[int, int]] = []
    for token in tokens:
        text = _token_text(token)
        if (
            output
            and _is_ascii_letter(output[-1])
            and text
            and _is_ascii_letter(text[0])
        ):
            output += " "
        start = len(output)
        output += text
        ranges.append((start, len(output)))
    return output, ranges


def _token_text(token: Any) -> str:
    if isinstance(token, dict):
        value = token.get("text", "")
    elif isinstance(token, str):
        value = token
    else:
        value = getattr(token, "text", "")
    return "" if value is None else str(value)


def _is_ascii_letter(character: str) -> bool:
    return "A" <= character <= "Z" or "a" <= character <= "z"
