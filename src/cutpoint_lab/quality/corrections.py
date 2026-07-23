from __future__ import annotations

import copy
import secrets
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..io import read_json, write_json


def _match_normalize(value: str) -> str:
    """NFKC-normalize text and fold only ASCII letters for matching."""

    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character
        for character in normalized
    )


def _starts_with_combining_mark(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return bool(normalized) and unicodedata.category(normalized[0]).startswith("M")


def _normalized_text_map(value: str) -> tuple[str, list[int], list[int]]:
    """Return matching text plus normalized-index to source-span maps."""

    normalized_parts: list[str] = []
    source_starts: list[int] = []
    source_ends: list[int] = []
    cluster_start = 0
    for cluster_end in range(1, len(value) + 1):
        if cluster_end < len(value) and _starts_with_combining_mark(value[cluster_end]):
            continue
        normalized = _match_normalize(value[cluster_start:cluster_end])
        normalized_parts.append(normalized)
        source_starts.extend([cluster_start] * len(normalized))
        source_ends.extend([cluster_end] * len(normalized))
        cluster_start = cluster_end
    return "".join(normalized_parts), source_starts, source_ends


def _literal_spans(text: str, wrong: str) -> list[tuple[int, int]]:
    normalized_text, source_starts, source_ends = _normalized_text_map(text)
    normalized_wrong = _match_normalize(wrong)
    if not normalized_wrong:
        return []

    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        match_start = normalized_text.find(normalized_wrong, cursor)
        if match_start < 0:
            break
        match_end = match_start + len(normalized_wrong)
        source_start = source_starts[match_start]
        source_end = source_ends[match_end - 1]
        span = (source_start, source_end)
        if not spans or span != spans[-1]:
            spans.append(span)
        cursor = match_end
    return spans


def _resolved_matches(
    text: str,
    correction_set: CorrectionSet,
) -> list[tuple[int, int, int, int, str, str]]:
    """在原文上一次性决议匹配：同起点最长优先，随后按词典顺序。"""

    candidates: list[tuple[int, int, int, int, str, str]] = []
    for pair_index, pair in enumerate(correction_set.pairs):
        for wrong_index, wrong in enumerate(pair["wrong"]):
            for start, end in _literal_spans(text, wrong):
                if text[start:end] == pair["right"]:
                    continue
                candidates.append(
                    (start, end, pair_index, wrong_index, wrong, pair["right"])
                )
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2], item[3]))

    resolved: list[tuple[int, int, int, int, str, str]] = []
    cursor = 0
    for match in candidates:
        if match[0] < cursor:
            continue
        resolved.append(match)
        cursor = match[1]
    return resolved


def _replace_resolved(
    text: str,
    matches: list[tuple[int, int, int, int, str, str]],
) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end, _pair_index, _wrong_index, _wrong, right in matches:
        pieces.extend((text[cursor:start], right))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _validated_pair(pair: Any) -> dict[str, Any]:
    if not isinstance(pair, dict):
        raise ValueError("Each correction pair must be an object")
    if set(pair) != {"wrong", "right", "is_term"}:
        raise ValueError("Correction pair must contain wrong, right, and is_term")

    wrong_values = pair["wrong"]
    right = pair["right"]
    is_term = pair["is_term"]
    if not isinstance(wrong_values, list) or not wrong_values:
        raise ValueError("Correction pair wrong must be a non-empty list")
    if not all(isinstance(value, str) and value.strip() for value in wrong_values):
        raise ValueError("Correction aliases must be non-empty strings")
    if not isinstance(right, str) or not right.strip():
        raise ValueError("Correction right must be a non-empty string")
    if not isinstance(is_term, bool):
        raise ValueError("Correction is_term must be a boolean")

    deduplicated: list[str] = []
    seen: set[str] = set()
    for wrong in wrong_values:
        normalized = _match_normalize(wrong)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduplicated.append(wrong)
    return {"wrong": deduplicated, "right": right, "is_term": is_term}


class CorrectionSet:
    def __init__(self, pairs: list[dict[str, Any]] | None = None) -> None:
        self.pairs: list[dict[str, Any]] = []
        for pair in pairs or []:
            validated = _validated_pair(pair)
            for wrong in validated["wrong"]:
                self.add_pair(wrong, validated["right"], is_term=validated["is_term"])

    @classmethod
    def from_dict(cls, payload: Any) -> CorrectionSet:
        if not isinstance(payload, dict) or set(payload) != {"pairs"}:
            raise ValueError("Correction dictionary must contain only pairs")
        if not isinstance(payload["pairs"], list):
            raise ValueError("Correction dictionary pairs must be a list")
        return cls(payload["pairs"])

    @classmethod
    def load(cls, path: str | Path) -> CorrectionSet:
        input_path = Path(path)
        if not input_path.exists():
            return cls()
        return cls.from_dict(read_json(input_path))

    def to_dict(self) -> dict[str, Any]:
        return {"pairs": copy.deepcopy(self.pairs)}

    def save(self, path: str | Path) -> None:
        write_json(path, self.to_dict())

    def add_pair(self, wrong: str, right: str, *, is_term: bool = False) -> None:
        validated = _validated_pair(
            {"wrong": [wrong], "right": right, "is_term": is_term}
        )
        normalized_wrong = _match_normalize(validated["wrong"][0])

        for pair in self.pairs:
            existing_aliases = {
                _match_normalize(alias): alias for alias in pair["wrong"]
            }
            if normalized_wrong in existing_aliases:
                if pair["right"] != right:
                    raise ValueError("Correction alias already maps to a different value")
                pair["is_term"] = pair["is_term"] or is_term
                return

        for pair in self.pairs:
            if pair["right"] == right:
                pair["wrong"].append(validated["wrong"][0])
                pair["is_term"] = pair["is_term"] or is_term
                return

        self.pairs.append(validated)


def preview_corrections(
    rows: list[dict[str, Any]], correction_set: CorrectionSet
) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for row in rows:
        text = row.get("text")
        if not isinstance(text, str):
            continue
        segment_id = str(row.get("id", row.get("segment_id", "")))
        matches = _resolved_matches(text, correction_set)
        for pair_index, pair in enumerate(correction_set.pairs):
            for wrong_index, wrong in enumerate(pair["wrong"]):
                count = sum(
                    1
                    for match in matches
                    if match[2] == pair_index and match[3] == wrong_index
                )
                if count:
                    preview.append(
                        {
                            "segment_id": segment_id,
                            "wrong": wrong,
                            "right": pair["right"],
                            "count": count,
                            "context": text,
                        }
                    )
    return preview


def new_changeset(label: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    change_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{secrets.token_hex(4)}"
    return {
        "change_id": change_id,
        "label": str(label),
        "changes": changes,
        "applied_at": now.isoformat(timespec="milliseconds"),
    }


def apply_corrections(
    rows: list[dict[str, Any]], correction_set: CorrectionSet
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    new_rows = copy.deepcopy(rows)
    changes: list[dict[str, Any]] = []
    total_replacements = 0

    for row in new_rows:
        original = row.get("text")
        if not isinstance(original, str):
            continue
        matches = _resolved_matches(original, correction_set)
        corrected = _replace_resolved(original, matches)
        row_replacements = len(matches)
        if corrected == original:
            continue

        row["text"] = corrected
        total_replacements += row_replacements
        changes.append(
            {
                "segment_id": str(row.get("id", row.get("segment_id", ""))),
                "field": "text",
                "old": original,
                "new": corrected,
            }
        )

    return new_rows, new_changeset(f"纠错词典 {total_replacements} 处", changes)


def undo_changeset(
    rows: list[dict[str, Any]], changeset: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    restored_rows = copy.deepcopy(rows)
    rows_by_id = {
        str(row.get("id", row.get("segment_id", ""))): row for row in restored_rows
    }
    skipped: list[dict[str, Any]] = []
    reverted = 0

    for change in reversed(changeset.get("changes", [])):
        segment_id = str(change.get("segment_id", ""))
        field = change.get("field")
        row = rows_by_id.get(segment_id)
        if row is None:
            skipped.append({"segment_id": segment_id, "reason": "row_not_found"})
            continue
        if row.get(field) != change.get("new"):
            skipped.append(
                {"segment_id": segment_id, "reason": "current_value_mismatch"}
            )
            continue
        row[field] = copy.deepcopy(change.get("old"))
        reverted += 1

    skipped.reverse()
    return restored_rows, {"reverted": reverted, "skipped": skipped}


def _safe_change_id(change_id: Any) -> str:
    if not isinstance(change_id, str) or not change_id:
        raise ValueError("ChangeSet change_id must be a non-empty string")
    if Path(change_id).name != change_id or change_id in {".", ".."}:
        raise ValueError("Invalid ChangeSet change_id")
    return change_id


def save_changeset(project_dir: str | Path, changeset: dict[str, Any]) -> Path:
    change_id = _safe_change_id(changeset.get("change_id"))
    output_path = Path(project_dir) / "changesets" / f"{change_id}.json"
    write_json(output_path, changeset)
    return output_path


def load_changeset(project_dir: str | Path, change_id: str) -> dict[str, Any]:
    safe_change_id = _safe_change_id(change_id)
    return read_json(Path(project_dir) / "changesets" / f"{safe_change_id}.json")
