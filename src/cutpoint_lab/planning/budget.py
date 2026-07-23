from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

EDL_ROLES = {"quote", "claim", "background", "support", "filler"}
FIT_STRATEGIES = {"strict", "complete", "keep_quotes"}
BRIEF_FIELDS = {
    "claim",
    "background",
    "audience",
    "must_keep",
    "avoid",
    "target_duration_s",
    "tolerance_s",
}
BRIEF_LIST_FIELDS = {"background", "must_keep", "avoid"}
BRIEF_TEXT_FIELDS = {"claim", "audience"}


def plan_duration_ms(plan: dict[str, Any]) -> int:
    total = 0
    for item in plan.get("ranges") or []:
        if not isinstance(item, dict):
            continue
        try:
            start_ms = int(item.get("start_ms", 0))
            end_ms = int(item.get("end_ms", 0))
        except (TypeError, ValueError):
            continue
        total += max(0, end_ms - start_ms)
    return total


def _selected_ids(edl: dict[str, Any]) -> list[str]:
    rows = [
        row
        for row in edl.get("rows") or []
        if isinstance(row, dict) and row.get("id") is not None
    ]
    known = {str(row["id"]) for row in rows}
    order = edl.get("order")
    if isinstance(order, list) and order:
        return [str(item) for item in order if str(item) in known]
    return [str(row["id"]) for row in rows if bool(row.get("checked"))]


def _estimate(
    edl: dict[str, Any],
    plan_builder: Callable[[dict[str, Any]], dict[str, Any]],
) -> int:
    if not _selected_ids(edl):
        return 0
    return plan_duration_ms(plan_builder(edl))


def _without(edl: dict[str, Any], segment_id: str) -> dict[str, Any]:
    updated = copy.deepcopy(edl)
    for row in updated.get("rows") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == segment_id:
            row["checked"] = False
    had_explicit_order = (
        isinstance(updated.get("order"), list)
        and bool(updated.get("order"))
    )
    if isinstance(updated.get("order"), list):
        updated["order"] = [
            str(item)
            for item in updated["order"]
            if str(item) != segment_id
        ]
    if had_explicit_order and not updated.get("order"):
        # 非空 order 原本是保留集权威；删掉最后一个播放项后不能回退到
        # rows[].checked，否则虚拟删除会把原本不在 order 的行重新选中。
        for row in updated.get("rows") or []:
            if isinstance(row, dict):
                row["checked"] = False
    return updated


def _seconds(raw: Any, field: str, *, optional: bool) -> int | float | None:
    if raw is None and optional:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        raise ValueError(f"brief.{field} 必须是非负数")
    return raw


def budget_report(
    edl: dict[str, Any],
    *,
    plan_builder: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(edl, dict):
        raise ValueError("EDL 必须是 JSON 对象")
    rows = edl.get("rows")
    if not isinstance(rows, list):
        raise ValueError("EDL 的 rows 必须是数组")
    brief = edl.get("brief")
    if brief is None:
        brief = {}
    if not isinstance(brief, dict):
        raise ValueError("EDL.brief 必须是对象")
    target_s = _seconds(
        brief.get("target_duration_s"),
        "target_duration_s",
        optional=True,
    )
    tolerance_s = _seconds(
        brief.get("tolerance_s", 0),
        "tolerance_s",
        optional=False,
    )
    estimated_ms = _estimate(edl, plan_builder)
    selected = set(_selected_ids(edl))
    report_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        row_id = str(row["id"])
        checked = row_id in selected
        without_ms = (
            _estimate(_without(edl, row_id), plan_builder)
            if checked
            else estimated_ms
        )
        role = str(row.get("role") or "support")
        if role not in EDL_ROLES:
            role = "support"
        report_rows.append(
            {
                "id": row_id,
                "ms": max(0, estimated_ms - without_ms) if checked else 0,
                "role": role,
                "locked": row.get("locked") is True,
                "checked": checked,
            }
        )
    target_ms = None if target_s is None else round(float(target_s) * 1000)
    return {
        "target_s": target_s,
        "tolerance_s": tolerance_s,
        "estimated_ms": estimated_ms,
        "delta_ms": None if target_ms is None else estimated_ms - target_ms,
        "rows": report_rows,
    }


def fit_budget(
    edl: dict[str, Any],
    *,
    strategy: str,
    plan_builder: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    if strategy not in FIT_STRATEGIES:
        raise ValueError("strategy 只能是 strict、complete 或 keep_quotes")
    report = budget_report(edl, plan_builder=plan_builder)
    target_s = report["target_s"]
    if target_s is None:
        raise ValueError("EDL.brief.target_duration_s 未设置")
    limit_ms = round(
        (float(target_s) + float(report["tolerance_s"] or 0)) * 1000
    )
    target_ms = round(float(target_s) * 1000)
    if strategy == "strict":
        allowed = ["filler", "support", "background", "claim"]
    elif strategy == "complete":
        allowed = ["filler", "support"]
    else:
        allowed = ["filler", "support", "background"]
    priorities = {role: index for index, role in enumerate(allowed)}
    candidates = [
        (index, row)
        for index, row in enumerate(report["rows"])
        if row["checked"]
        and not row["locked"]
        and row["role"] in priorities
        and row["role"] != "quote"
    ]
    projected_edl = copy.deepcopy(edl)
    projected_ms = report["estimated_ms"]
    suggestions: list[dict[str, Any]] = []
    if strategy == "keep_quotes":
        remaining = list(candidates)
        while projected_ms > limit_ms and remaining:
            evaluated: list[
                tuple[int, int, int, dict[str, Any], dict[str, Any]]
            ] = []
            for index, row in remaining:
                candidate_edl = _without(projected_edl, row["id"])
                candidate_ms = _estimate(candidate_edl, plan_builder)
                saving_ms = max(0, projected_ms - candidate_ms)
                if saving_ms <= 0:
                    continue
                evaluated.append(
                    (
                        abs(candidate_ms - target_ms),
                        priorities[row["role"]],
                        index,
                        row,
                        candidate_edl,
                    )
                )
            if not evaluated:
                break
            _gap, _priority, chosen_index, row, candidate_edl = min(
                evaluated,
                key=lambda item: (item[0], item[1], item[2]),
            )
            candidate_ms = _estimate(candidate_edl, plan_builder)
            suggestions.append(
                {
                    "id": row["id"],
                    "ms": max(0, projected_ms - candidate_ms),
                    "role": row["role"],
                    "reason": (
                        "keep_quotes 策略保留 quote/claim，选择最接近目标的未锁定删减"
                    ),
                }
            )
            projected_edl = candidate_edl
            projected_ms = candidate_ms
            remaining = [
                item for item in remaining if item[0] != chosen_index
            ]
    else:
        candidates.sort(
            key=lambda item: (priorities[item[1]["role"]], item[0])
        )
    for _index, row in candidates:
        if strategy == "keep_quotes":
            break
        if projected_ms <= limit_ms:
            break
        candidate_edl = _without(projected_edl, row["id"])
        candidate_ms = _estimate(candidate_edl, plan_builder)
        saving_ms = max(0, projected_ms - candidate_ms)
        if saving_ms <= 0:
            continue
        suggestions.append(
            {
                "id": row["id"],
                "ms": saving_ms,
                "role": row["role"],
                "reason": f"{strategy} 策略优先移除 {row['role']}，且未锁定",
            }
        )
        projected_edl = candidate_edl
        projected_ms = candidate_ms

    result: dict[str, Any] = {
        "suggestions": suggestions,
        "projected_ms": projected_ms,
    }
    if strategy == "strict":
        result["infeasible"] = projected_ms > limit_ms
        if projected_ms > limit_ms:
            result["gap_ms"] = projected_ms - limit_ms
    elif strategy == "complete":
        result["overage_ms"] = max(0, projected_ms - limit_ms)
    elif projected_ms > limit_ms:
        result["infeasible"] = True
        result["gap_ms"] = projected_ms - limit_ms
    return result


def update_brief(
    current: dict[str, Any] | None,
    patch: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("brief 更新内容必须是对象")
    unknown = set(patch) - BRIEF_FIELDS
    if unknown:
        raise ValueError("未知 brief 字段：" + "、".join(sorted(unknown)))
    result = copy.deepcopy(current) if isinstance(current, dict) else {}
    for field, raw in patch.items():
        if raw is None:
            result.pop(field, None)
            continue
        if field in BRIEF_TEXT_FIELDS:
            if not isinstance(raw, str):
                raise ValueError(f"brief.{field} 必须是字符串")
            result[field] = raw
        elif field in BRIEF_LIST_FIELDS:
            if not isinstance(raw, list) or not all(
                isinstance(item, str) for item in raw
            ):
                raise ValueError(f"brief.{field} 必须是字符串数组")
            result[field] = list(raw)
        else:
            result[field] = _seconds(raw, field, optional=False)
    return result


__all__ = [
    "BRIEF_FIELDS",
    "EDL_ROLES",
    "FIT_STRATEGIES",
    "budget_report",
    "fit_budget",
    "plan_duration_ms",
    "update_brief",
]
