"""变压器监视接口：维护变压器，提交读数判定，保存油温上限等参数。

判定口径在 service/judgment 里，路由层不做业务判断；/export 必须注册在
/{entry_id} 之前，否则 "export" 会被当成设备编号拦截，导出条数随之错位。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.transformer import STATUS_ORDER, TransformerService

router = APIRouter(prefix="/api/transformer", tags=["变压器监视"])

service = TransformerService()

LIST_FIELDS = ["变压器编号", "电压等级", "额定容量", "油温上限", "绕组温度", "油位状态", "瓦斯保护状态", "运行状态"]
STATUSES = STATUS_ORDER


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按变压器编号检索"),
    status: str | None = Query(default=None, description="正常运行、油温偏高、高温报警"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按变压器编号与状态过滤变压器监视列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按变压器编号检索"),
    status: str | None = Query(default=None, description="正常运行、油温偏高、高温报警"),
) -> dict[str, Any]:
    """导出变压器监视清单：与列表同一套过滤口径，total 与 items 条数严格一致。"""
    items, total = service.list_entries(keyword=keyword, status=status, page=1, size=100000)
    return {"module": "transformer", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条变压器明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"变压器 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条变压器，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="变压器已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """保存油温上限等静态参数，落库后列表与详情读到的是同一份新值。"""
    entry, message = service.update_settings(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/judgment", response_model=ActionResult)
def judge_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """提交油温/绕组温度/油位/瓦斯读数执行统一判定：结论落库、逐档流转、重复提交只生效一次。"""
    entry, message, applied = service.judge(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=applied, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条变压器执行恢复正常；状态机逐档回落，不会一次跨回正常运行。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
