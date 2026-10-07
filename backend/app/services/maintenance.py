"""检修计划业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "maintenance"
REQUIRED_FIELDS = ["计划编号", "检修设备", "检修类别"]
STATUS_ORDER = ["待审批", "已批复", "执行中", "已完工"]
ACTION_RULES = {"提交审批": "已批复", "开始执行": "执行中", "确认完工": "已完工"}
NEGATIVE_ACTIONS = []


class MaintenanceService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("计划编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检修计划 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检修计划可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检修计划已{action}"

    def sync_transformer_oil_level(
        self,
        *,
        transformer_id: int,
        device_name: str,
        oil_level: str,
        verdict: str,
    ) -> dict[str, Any]:
        """把变压器「油位异常」结论同步到检修计划台账（upsert）。

        同一台变压器只对应一条台账记录（按关联编号关联），重复同步就地更新，
        台账里的油位读数始终与变压器判定结果保持一致。
        """
        rows = store.rows(MODULE)
        plan_no = f"MAIN-TRAN-{transformer_id:04d}"
        entry = next(
            (row for row in rows if row.get("关联编号") == plan_no),
            None,
        )
        if entry is None:
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            rows.append(entry)

        entry.update({
            "计划编号": plan_no,
            "关联编号": plan_no,
            "来源模块": "transformer",
            "检修设备": device_name,
            "检修类别": "油位异常处理",
            "计划状态": verdict,
            "油位读数": oil_level,
            "status": "待审批",
            "pending": True,
            "abnormal": True,
        })
        return entry
