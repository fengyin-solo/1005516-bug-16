"""变压器判定口径：全平台只此一份，列表、详情、入库与台账同步都以这里为准。

口径约定（油温上限、绕组温度、油位状态、瓦斯保护状态）：

- 油温：以「油温上限」为阈值，拿当前「绕组温度」去比。
  * 绕组温度达到或超过油温上限   -> 高温报警
  * 绕组温度达到上限的 90% 但未越限 -> 油温偏高
  * 其余（含读数缺失、无法解析）  -> 正常运行
- 油位：「油位状态」读到偏低（低/低油位/偏低/low 等）-> 油位异常
- 瓦斯保护：「瓦斯保护状态」读到动作/告警/跳闸等 -> 与高温报警同档处理
- 档位互斥不重叠：高温报警与油位异常同时成立时，按更重的「高温报警」走。

判定结果只有三档运行状态：正常运行 -> 油温偏高 -> 高温报警。
「油位异常」是与高温报警互斥的结论（更重档优先），落在油位状态字段上，
不单独占用运行状态档位，因此不会和温度档位重叠。
"""
from __future__ import annotations

from typing import Any

# 三档运行状态及其由轻到重的顺序，状态流转只能顺着这个序列逐档走。
STATUS_ORDER = ["正常运行", "油温偏高", "高温报警"]

# 判定结论（互斥，冲突时取更重档）。
VERDICT_NORMAL = "正常运行"
VERDICT_OIL_LOW = "油位异常"
VERDICT_HIGH_TEMP = "高温报警"

# 结论严重度：数值越大越重，冲突时取大值。
SEVERITY = {VERDICT_NORMAL: 0, VERDICT_OIL_LOW: 1, VERDICT_HIGH_TEMP: 2}

# 油温偏高的起步比例：达到上限的 90% 视为偏高。
WARM_RATIO = 0.9

# 油位 / 瓦斯保护的可读值。
OIL_LEVEL_LOW = "偏低"
OIL_LEVEL_NORMAL = "正常"
GAS_TRIPPED = "动作"
GAS_NORMAL = "正常"

_OIL_LOW_TOKENS = ("低", "low")
_GAS_TRIP_TOKENS = ("动作", "告警", "报警", "跳闸", "trip", "alarm")
_NORMAL_TOKENS = ("正常", "normal", "ok", "良好", "未动作")


def to_float(value: Any) -> float | None:
    """把读数宽松地转成数值；空值、占位文本、非数字一律视为读不到，按正常处理。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _hits(value: Any, tokens: tuple[str, ...]) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and any(token.lower() in text for token in tokens)


def is_oil_low(oil_level: Any) -> bool:
    """油位是否偏低；明确写「正常」的不算低，其余按偏低关键词识别。"""
    if _hits(oil_level, _NORMAL_TOKENS) and not _hits(oil_level, ("低",)):
        return False
    return _hits(oil_level, _OIL_LOW_TOKENS)


def is_gas_tripped(gas_status: Any) -> bool:
    """瓦斯保护是否动作/告警。"""
    return _hits(gas_status, _GAS_TRIP_TOKENS)


def evaluate_transformer(
    *,
    oil_temp_limit: Any,
    winding_temp: Any,
    oil_level: Any,
    gas_status: Any,
) -> dict[str, str]:
    """按统一口径给出变压器判定结果。

    返回运行状态 status、结论 verdict，以及归一化后的油位/瓦斯可读值，
    供入库与列表/详情/台账共同使用，保证两处读数一致。
    """
    limit = to_float(oil_temp_limit)
    current = to_float(winding_temp)

    # 温度档位（运行状态）。
    if limit is not None and current is not None and limit > 0:
        if current >= limit:
            temp_status = "高温报警"
        elif current >= limit * WARM_RATIO:
            temp_status = "油温偏高"
        else:
            temp_status = "正常运行"
    else:
        temp_status = "正常运行"

    oil_low = is_oil_low(oil_level)
    gas_tripped = is_gas_tripped(gas_status)

    # 互斥结论候选，取最严重的一档；瓦斯动作按高温报警处理。
    candidates = [VERDICT_NORMAL]
    if oil_low:
        candidates.append(VERDICT_OIL_LOW)
    if temp_status == "高温报警" or gas_tripped:
        candidates.append(VERDICT_HIGH_TEMP)
    verdict = max(candidates, key=lambda name: SEVERITY[name])

    # 瓦斯动作落到运行状态的最重档（高温报警）；否则保留温度档位。
    if gas_tripped:
        status = "高温报警"
    else:
        status = temp_status

    return {
        "status": status,
        "verdict": verdict,
        "oil_level": OIL_LEVEL_LOW if oil_low else OIL_LEVEL_NORMAL,
        "gas_status": GAS_TRIPPED if gas_tripped else GAS_NORMAL,
    }


def normalize_status(status: Any) -> str:
    """兼容既有设备数据：旧状态名映射到三档口径，认不出的一律按正常运行。"""
    legacy = {
        "正常运行": "正常运行",
        "油温偏高": "油温偏高",
        "油温异常": "油温偏高",
        "高温报警": "高温报警",
    }
    return legacy.get(str(status or "").strip(), "正常运行")
