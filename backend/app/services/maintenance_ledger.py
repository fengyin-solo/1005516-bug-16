"""检修台账同步：变压器油位异常结论在这里落到检修计划台账。

变压器判定为油位偏低时按设备编号 upsert 一条“油位异常处理”计划，后续判定
只刷新读数快照（两处读数保持同步）；油位恢复正常时把计划收尾为已完工，
不会重复新建台账，也不会留下读数对不上的悬挂记录。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "maintenance"
PLAN_PREFIX = "OIL-"
CATEGORY = "油位异常处理"


def _find_plan(rows: list[dict[str, Any]], device_code: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("来源设备") == device_code and str(row.get("检修类别") or "") == CATEGORY:
            return row
    return None


def _next_plan_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_plan_code(rows: list[dict[str, Any]]) -> str:
    tail = max((int(str(row.get("计划编号", "")).removeprefix(PLAN_PREFIX))
                for row in rows if str(row.get("计划编号", "")).startswith(PLAN_PREFIX)), default=0)
    return f"{PLAN_PREFIX}{tail + 1:04d}"


def sync_oil_level_ledger(
    *,
    device_code: str,
    oil_level: str,
    oil_low: bool,
    reading_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """按设备编号同步油位异常台账，返回被新建/更新的那一行（未变更时返回已有行）。"""
    rows = store.rows(MODULE)
    plan = _find_plan(rows, device_code)
    snapshot_text = "，".join(f"{key}:{value if value is not None else '—'}"
                              for key, value in reading_snapshot.items())

    if oil_low:
        if plan is None:
            plan = {
                "id": _next_plan_id(rows),
                "计划编号": _next_plan_code(rows),
                "检修设备": f"变压器 {device_code}",
                "来源设备": device_code,
                "检修类别": CATEGORY,
                "计划开始": "",
                "计划结束": "",
                "责任人": "",
                "安全措施": "核对油位、排查渗漏并补油",
                "计划状态": "待安排",
                "status": "待安排",
                "pending": True,
                "abnormal": True,
            }
            rows.append(plan)
        # 每次判定都刷新读数，保证台账与变压器两处读数同步。
        plan["油位状态"] = oil_level
        plan["最新读数"] = snapshot_text
        if plan.get("计划状态") != "已完工":
            plan["计划状态"] = "待安排"
            plan["status"] = "待安排"
            plan["pending"] = True
            plan["abnormal"] = True
        return dict(plan)

    # 油位恢复：把既有异常计划收尾；从未异常过则不凭空造台账。
    if plan is not None:
        plan["油位状态"] = oil_level
        plan["最新读数"] = snapshot_text
        plan["计划状态"] = "已完工"
        plan["status"] = "已完工"
        plan["pending"] = False
        plan["abnormal"] = False
    return dict(plan) if plan is not None else {}
