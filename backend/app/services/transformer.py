"""变压器监视业务规则：判定口径、状态流转、字段校验与筛选都收在这里。

判定规则统一来自 app.services.evaluation，本模块只负责取数、入库、
状态流转与台账同步，避免列表、详情、导出各算各的。
"""
from __future__ import annotations

from typing import Any

from app.services.evaluation import STATUS_ORDER, evaluate_transformer, normalize_status
from app.services.maintenance import MaintenanceService
from app.store import store

MODULE = "transformer"
REQUIRED_FIELDS = ["变压器编号", "电压等级", "额定容量"]
# 可登记/可更新的监测字段（判定输入与结论展示都在这里）。
MONITOR_FIELDS = ["油温上限", "绕组温度", "油位状态", "瓦斯保护状态"]

# 三档顺序：正常运行 -> 油温偏高 -> 高温报警，只能逐档流转，不能跨档。
ACTION_RULES = {"恢复正常": "正常运行"}

_RANK = {name: index for index, name in enumerate(STATUS_ORDER)}


class TransformerService:
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
            rows = [row for row in rows if keyword in str(row.get("变压器编号", ""))]
        if status:
            rows = [row for row in rows if normalize_status(row.get("status")) == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._view(row) for row in rows[start:start + size]], total

    def list_all(self) -> list[dict[str, Any]]:
        return [self._view(row) for row in store.rows(MODULE)]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._view(row) if row is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in MONITOR_FIELDS:
            entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["judgement_fingerprint"] = ""
        rows.append(entry)
        return self._view(entry), []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """更新变压器档案（含油温上限等监测字段），改动立即入库。

        仅更新油温上限等阈值、不会重算状态；要触发判定请走 submit_judgement。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变压器 {entry_id} 不存在或已归档"
        changed = False
        for field in REQUIRED_FIELDS + MONITOR_FIELDS:
            if field in values and str(values.get(field) or "").strip():
                entry[field] = values.get(field)
                changed = True
        if not changed:
            return None, "没有可更新的字段"
        return self._view(entry), "变压器档案已更新"

    def submit_judgement(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """提交一次判定读数：按统一口径判定并入库。

        返回 (记录, 说明, 是否已生效)。同一台设备用同一份读数重复提交，
        只生效一次（指纹幂等），重复提交直接回读现有结论，不改状态、不重复记账。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变压器 {entry_id} 不存在或已归档", False

        # 本次提交的读数：提交值优先，缺省沿用库里既有读数。
        readings = {
            "油温上限": values.get("油温上限", entry.get("油温上限")),
            "绕组温度": values.get("绕组温度", entry.get("绕组温度")),
            "油位状态": values.get("油位状态", entry.get("油位状态")),
            "瓦斯保护状态": values.get("瓦斯保护状态", entry.get("瓦斯保护状态")),
        }
        fingerprint = "|".join(str(readings[field] or "") for field in readings)
        if fingerprint == entry.get("judgement_fingerprint"):
            return self._view(entry), "读数与上次一致，判定结果未重复生效", False

        result = evaluate_transformer(
            oil_temp_limit=readings["油温上限"],
            winding_temp=readings["绕组温度"],
            oil_level=readings["油位状态"],
            gas_status=readings["瓦斯保护状态"],
        )

        current = normalize_status(entry.get("status"))
        target = result["status"]
        # 状态只能沿 正常运行 -> 油温偏高 -> 高温报警 逐档向上，不允许跨档。
        if _RANK[target] > _RANK[current] and _RANK[target] != _RANK[current] + 1:
            return (
                None,
                f"状态不允许从「{current}」跨档到「{target}」，请按顺序逐档提交",
                False,
            )

        # 读数与结论入库。
        entry.update(readings)
        entry["status"] = target
        entry["油位状态"] = result["oil_level"]
        entry["瓦斯保护状态"] = result["gas_status"]
        entry["判定结论"] = result["verdict"]
        entry["abnormal"] = target != "正常运行" or result["verdict"] != "正常运行"
        entry["pending"] = target != "高温报警"
        entry["judgement_fingerprint"] = fingerprint

        # 油位异常结论同步检修计划台账，两处读数保持一致。
        if result["verdict"] == "油位异常":
            MaintenanceService().sync_transformer_oil_level(
                transformer_id=entry_id,
                device_name=str(entry.get("变压器编号", f"变压器 {entry_id}")),
                oil_level=result["oil_level"],
                verdict=result["verdict"],
            )

        return self._view(entry), f"判定完成：{result['verdict']}（{target}）", True

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变压器 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于变压器监视可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = True
        entry["abnormal"] = False
        # 复位后清掉指纹，允许设备恢复后用新读数重新判定。
        entry["judgement_fingerprint"] = ""
        return self._view(entry), f"变压器已{action}"

    def _view(self, entry: dict[str, Any]) -> dict[str, Any]:
        """列表、详情、导出统一出口：同一份字段，保证两处电压等级等完全一致。"""
        view = dict(entry)
        view["status"] = normalize_status(entry.get("status"))
        view["运行状态"] = view["status"]
        return view
