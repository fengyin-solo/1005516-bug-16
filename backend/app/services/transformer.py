"""变压器监视业务规则：判定、状态流转、字段校验与筛选口径都收在这里。

判定阈值/互斥/取重统一走 app.services.judgment，本模块只负责读数收集、
结论落库、相邻档状态机、重复提交去重，以及把油位异常同步到检修台账。
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from app.services.judgment import (
    DEFAULT_OIL_LIMIT,
    TRANSFORMER_STATUS_ORDER,
    judge_transformer,
    normalize_status,
    resolve_status_filter,
    to_float,
)
from app.services.maintenance_ledger import sync_oil_level_ledger
from app.store import store

MODULE = "transformer"
REQUIRED_FIELDS = ["变压器编号", "电压等级", "额定容量"]
# 允许通过保存接口落库的静态参数，判定读数不允许从这里混进来。
EDITABLE_FIELDS = ["电压等级", "额定容量", "油温上限", "绕组温度", "油位状态", "瓦斯保护状态"]
STATUS_ORDER = TRANSFORMER_STATUS_ORDER
# 兼容既有设备：动作名与旧状态机名保留映射，路由层传入时先归一化。
ACTION_RULES = {"恢复正常": "正常运行"}

OIL_LEVEL_NORMAL = "正常"
GAS_STATUS_NORMAL = "正常"


def _fingerprint(entry_id: int, values: dict[str, Any]) -> str:
    """同一台设备、同一组读数的指纹（与起始档位无关）。

    配合“上一次生效时的起始档”一起比对：设备停在同一档时重复交同一组读数才是
    重复提交；设备已经落到下一档再交同样读数属于继续推进，不算重复。
    """
    raw = "|".join(str(values.get(key, "")) for key in
                   ("油温", "绕组温度", "油位状态", "瓦斯保护状态", "油温上限"))
    return sha256(f"{entry_id}:{raw}".encode("utf-8")).hexdigest()


def _step_toward(current: str, target: str) -> str:
    """状态机逐档推进：同档不动，相邻档直达，差多档只走最近的一档，绝不跨档。"""
    cur_idx, target_idx = STATUS_ORDER.index(current), STATUS_ORDER.index(target)
    if target_idx > cur_idx:
        return STATUS_ORDER[min(cur_idx + 1, target_idx)]
    if target_idx < cur_idx:
        return STATUS_ORDER[max(cur_idx - 1, target_idx)]
    return current


def _present(entry: dict[str, Any]) -> dict[str, Any]:
    """列表与详情共用同一份投影：运行状态始终镜像 status，杜绝两处口径不一致。"""
    entry["运行状态"] = entry.get("status", STATUS_ORDER[0])
    entry.setdefault("油位状态", OIL_LEVEL_NORMAL)
    entry.setdefault("瓦斯保护状态", GAS_STATUS_NORMAL)
    entry.setdefault("油温上限", DEFAULT_OIL_LIMIT)
    return entry


class TransformerService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [_present(dict(row)) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("变压器编号", ""))]
        if status:
            wanted = resolve_status_filter(status)
            if wanted is None:
                return [], 0
            rows = [row for row in rows if row.get("status") == wanted]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return _present(dict(entry)) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry.update({
            "油温上限": to_float(values.get("油温上限")) or DEFAULT_OIL_LIMIT,
            "绕组温度": values.get("绕组温度"),
            "油位状态": str(values.get("油位状态") or OIL_LEVEL_NORMAL).strip(),
            "瓦斯保护状态": str(values.get("瓦斯保护状态") or GAS_STATUS_NORMAL).strip(),
            "status": STATUS_ORDER[0],
            "pending": False,
            "abnormal": False,
            "oil_low": False,
            "gas_trip": False,
            "判语": "",
        })
        rows.append(entry)
        return _present(dict(entry)), []

    def update_settings(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """保存静态参数（油温上限等）：先落库再投影，修掉“改完存一次又变回原数”。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变压器 {entry_id} 不存在或已归档"
        if "油温上限" in values:
            limit = to_float(values.get("油温上限"))
            if limit is None or limit <= 0:
                return None, "油温上限必须是大于 0 的数值"
            entry["油温上限"] = limit
        for field in EDITABLE_FIELDS:
            if field == "油温上限" or field not in values:
                continue
            value = values.get(field)
            if value is not None and str(value).strip() != "":
                entry[field] = value
        # 电压等级等参数一处落库，列表与详情同读此 row，天然一致。
        return _present(dict(entry)), "参数已保存"

    def judge(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, bool]:
        """提交一组读数执行判定，返回 (明细, 说明, 是否本次生效)。

        - 读数控温上限可随提交一起更新；
        - 同一台设备在同一档位上重复提交同一组读数只生效一次（幂等）；
        - 结论只能在 正常运行→油温偏高→高温报警 间逐档流转：跨档提交不报错，
          本次只向结论方向推进一档，连续提交即可到位，任何时候都不跨档；
        - 油位异常同步检修台账，两处读数始终同步。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变压器 {entry_id} 不存在或已归档", False

        # 先把可选的油温上限保存下来，保证判定用的就是这次提交的口径。
        if str(values.get("油温上限") or "").strip() != "":
            updated, message = self.update_settings(entry_id, {"油温上限": values["油温上限"]})
            if updated is None:
                return None, message, False

        result = judge_transformer(
            oil_temp=values.get("油温"),
            winding_temp=values.get("绕组温度"),
            oil_level=values.get("油位状态"),
            gas_status=values.get("瓦斯保护状态"),
            oil_limit=entry.get("油温上限"),
        )

        current = normalize_status(entry.get("status"))
        fingerprint = _fingerprint(entry_id, values)
        # 幂等：同一组读数、且它的判定结论就停在当前档（没有档位要推进）时，
        # 同一台设备重复提交只生效一次；还要升/降一档的提交照常放行。
        if entry.get("judged_fingerprint") == fingerprint and result.status == current:
            return _present(dict(entry)), "该组读数的判定结论已生效，重复提交未再处理", False

        # 逐档逼近判定结论：无论升温升级还是恢复降级，一次提交最多动一档。
        landed = _step_toward(current, result.status)

        # 判定结果落库：status 是唯一运行状态，运行状态/通道字段同源镜像。
        entry["status"] = landed
        entry["运行状态"] = landed
        entry["油位状态"] = result.readings["油位状态"]
        entry["瓦斯保护状态"] = result.readings["瓦斯保护状态"]
        for key in ("油温", "绕组温度"):
            if result.readings[key] is not None:
                entry[key] = result.readings[key]
        entry["oil_low"] = result.oil_low
        entry["gas_trip"] = result.gas_trip
        entry["abnormal"] = landed != STATUS_ORDER[0]
        entry["pending"] = landed != STATUS_ORDER[0]
        entry["判语"] = (
            result.summary if landed == result.status
            else f"{result.summary}；按状态序列本次先置为「{landed}」，再次提交可推进至「{result.status}」"
        )
        entry["judged_fingerprint"] = fingerprint

        # 油位异常结论同步检修台账；油位恢复时同步收尾，读数保持一致。
        sync_oil_level_ledger(
            device_code=str(entry.get("变压器编号") or entry_id),
            oil_level=entry["油位状态"],
            oil_low=result.oil_low,
            reading_snapshot={
                "油位状态": entry["油位状态"],
                "油温": result.readings["油温"],
                "绕组温度": result.readings["绕组温度"],
                "瓦斯保护状态": entry["瓦斯保护状态"],
            },
        )
        return _present(dict(entry)), f"判定完成：{entry['判语']}", True

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变压器 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于变压器监视可执行范围"
        target = ACTION_RULES[action]
        current = normalize_status(entry.get("status"))
        # 人工操作同样不跨档：一次回落一档，连续执行即可恢复正常运行。
        landed = _step_toward(current, target)
        entry["status"] = landed
        entry["运行状态"] = landed
        entry["pending"] = landed != STATUS_ORDER[0]
        entry["abnormal"] = landed != STATUS_ORDER[0]
        entry["judged_fingerprint"] = ""
        entry["判语"] = f"人工{action}" if landed == target else f"人工{action}：本次回落至「{landed}」"
        return _present(dict(entry)), f"变压器已{landed}"
