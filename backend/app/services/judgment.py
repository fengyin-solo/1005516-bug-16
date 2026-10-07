"""设备判定口径（全平台唯一一份）。

阈值判级、通道互斥、冲突取重、状态机相邻流转都收在这里，各设备服务直接调用，
不在自己模块里另写一套判断，避免同一组读数在列表、详情、导出里判出两种结论。

变压器口径（兼容既有设备的历史状态名）：
- 油温达到油温上限的 90% 但未超限：油温偏高；
- 油温（或绕组温度）超过油温上限、瓦斯保护动作：高温报警；
- 油位偏低：油位异常（独立结论，同步检修台账）。

温度三档与油位异常两档不许并列：冲突时只保留严重度更高的那一档。
状态机固定为 正常运行 → 油温偏高 → 高温报警，升降级都只能逐档走，不能跨档。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# 变压器运行状态机：顺序即严重度，index 差超过 1 即视为跨档。
TRANSFORMER_STATUS_ORDER = ["正常运行", "油温偏高", "高温报警"]

# 默认油温上限（℃）与预警系数：达到上限的 WARNING_RATIO 且未超限记“油温偏高”。
DEFAULT_OIL_LIMIT = 85.0
WARNING_RATIO = 0.9

# 严重度字典：数字越大越重，冲突时整张表只保留最重的一档。
SEVERITY_RANK = {"正常运行": 0, "油温偏高": 1, "高温报警": 2}

# 既有设备/历史数据里的旧状态名 → 新状态机口径，保证老数据也能接着判。
LEGACY_STATUS = {
    "正常": "正常运行",
    "过负荷": "油温偏高",
    "油温异常": "高温报警",
    "待检修": "高温报警",
}

# 读数文本归一化：现场可能填“偏低/过低/动作/跳闸”等不同说法。
OIL_LOW_TOKENS = {"偏低", "低", "过低", "缺油", "异常"}
GAS_TRIP_TOKENS = {"动作", "跳闸", "报警", "告警", "异常"}


def to_float(value: Any) -> float | None:
    """把前端传来的读数尽量转成数值；空串/非数值返回 None，不参与判级。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def text_hits(value: Any, tokens: set[str]) -> bool:
    text = str(value or "").strip()
    return bool(text) and any(token in text for token in tokens)


def normalize_status(status: Any) -> str:
    """把任意历史/外部状态名归并到当前状态机；无法识别时按最保守的正常档处理。"""
    text = str(status or "").strip()
    return LEGACY_STATUS.get(text, text if text in TRANSFORMER_STATUS_ORDER else "正常运行")


def resolve_status_filter(status: Any) -> str | None:
    """解析列表过滤用的状态：识别当前名与历史名，无法识别时返回 None（匹配 0 条）。

    与 normalize_status 区分：判定面对的是设备自身的脏数据，未知按正常兜底；
    过滤面对的是用户输入，未知档不能悄悄变成“正常运行”。
    """
    text = str(status or "").strip()
    if text in TRANSFORMER_STATUS_ORDER:
        return text
    return LEGACY_STATUS.get(text)


def can_transition(current: str, target: str, order: list[str] | None = None) -> bool:
    """状态流转只能按序列逐档走：同档（重复判定）或相邻档放行，跨档拦截。"""
    order = order or TRANSFORMER_STATUS_ORDER
    if current not in order or target not in order:
        return False
    return abs(order.index(current) - order.index(target)) <= 1


def evaluate(ordered_levels: list[tuple[str, Callable[[], bool]]]) -> str:
    """通用互斥判级：按严重度从高到低给出 (档位名, 命中谓词)，第一个命中的即结论。

    各档位谓词从上往下写，保证两档永远不会同时命中——需要“不重叠、取最重”的
    既有设备都可以复用本函数。
    """
    for name, predicate in ordered_levels:
        if predicate():
            return name
    return ordered_levels[-1][0]


@dataclass
class Judgment:
    """一次判定的归一化结论：单一运行状态 + 各通道事实 + 可读判语。"""

    status: str
    oil_low: bool = False
    gas_trip: bool = False
    reasons: list[str] = field(default_factory=list)
    readings: dict[str, Any] = field(default_factory=dict)

    @property
    def severity(self) -> int:
        return SEVERITY_RANK[self.status]

    @property
    def summary(self) -> str:
        return "；".join(self.reasons) if self.reasons else "各通道读数正常"


def judge_transformer(
    *,
    oil_temp: Any = None,
    winding_temp: Any = None,
    oil_level: Any = None,
    gas_status: Any = None,
    oil_limit: Any = None,
) -> Judgment:
    """按唯一口径判定一台变压器，返回互斥的单一状态与各通道事实。

    温度通道内部三档互斥（超限→高温报警，达预警线→油温偏高，否则正常）；
    瓦斯动作、油位偏低按最高档严重度参与比较，最终状态只保留最重的一档。
    """
    limit = to_float(oil_limit)
    if limit is None or limit <= 0:
        limit = DEFAULT_OIL_LIMIT
    warn_line = limit * WARNING_RATIO

    oil = to_float(oil_temp)
    winding = to_float(winding_temp)
    oil_low = text_hits(oil_level, OIL_LOW_TOKENS)
    gas_trip = text_hits(gas_status, GAS_TRIP_TOKENS)

    # 温度通道：谓词从最重档往下排，天然互斥不重叠。
    def over_limit() -> bool:
        return any(value is not None and value >= limit for value in (oil, winding))

    def over_warn() -> bool:
        return any(value is not None and value >= warn_line for value in (oil, winding))

    temp_status = evaluate([
        ("高温报警", over_limit),
        ("油温偏高", over_warn),
        ("正常运行", lambda: True),
    ])

    reasons: list[str] = []
    if over_limit():
        hit = []
        if oil is not None and oil >= limit:
            hit.append(f"油温 {oil:g}℃≥上限 {limit:g}℃")
        if winding is not None and winding >= limit:
            hit.append(f"绕组温度 {winding:g}℃≥上限 {limit:g}℃")
        reasons.append("高温报警（" + "，".join(hit) + "）")
    elif temp_status == "油温偏高":
        reasons.append(f"油温偏高（达到预警线 {warn_line:g}℃，上限 {limit:g}℃）")

    if gas_trip:
        reasons.append(f"瓦斯保护{str(gas_status).strip()}")
    if oil_low:
        reasons.append(f"油位异常（油位{str(oil_level).strip()}）")

    # 冲突取重：瓦斯动作、油位偏低都按最高档严重度与温度档比较，只保留一个状态。
    severity = SEVERITY_RANK[temp_status]
    if gas_trip or oil_low:
        severity = max(severity, SEVERITY_RANK["高温报警"])
    status = TRANSFORMER_STATUS_ORDER[severity]
    if not reasons:
        reasons.append("油温、绕组温度、油位、瓦斯保护均在正常范围")

    return Judgment(
        status=status,
        oil_low=oil_low,
        gas_trip=gas_trip,
        reasons=reasons,
        readings={
            "油温上限": limit,
            "油温": oil,
            "绕组温度": winding,
            "油位状态": str(oil_level or "").strip() or "正常",
            "瓦斯保护状态": str(gas_status or "").strip() or "正常",
        },
    )
