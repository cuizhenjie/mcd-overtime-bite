"""触发引擎：**什么情况下才该动手点餐**。

这是 OvertimeBite 与其它参赛项目的第一个差异点。
多数"省钱点餐"项目从用户主动发起提问开始；OvertimeBite 从**时间自己走到某个点**开始。

一个合格的自动触发器必须回答三个问题：
1. 现在是时候了吗？（时间窗口 + 加班证据）
2. 今天是不是已经点过了？（幂等）
3. 我有没有权限直接付钱？（自动化授权边界）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .config import OvertimeConfig
from .models import TriggerState


class Action(str, Enum):
    ORDER = "order"            # 满足全部硬约束，可自动下单
    CONFIRM = "confirm"        # 需要人工确认后才能下单
    SILENT = "silent"          # 不打扰，只记一条建议
    SKIP = "skip"              # 不触发


@dataclass(frozen=True)
class TriggerDecision:
    action: Action
    reason: str
    window: str

    @property
    def should_act(self) -> bool:
        return self.action in (Action.ORDER, Action.CONFIRM)


def _window_of(hour: int) -> str:
    """把小时归并到粗粒度时间窗，作为幂等键的一部分。

    粗粒度是为了避免"18:30 触发失败后 19:05 又触发一次"这种重复下单。
    """
    return f"dinner-{hour // 4 * 4:02d}"


def evaluate(
    now: datetime,
    state: TriggerState,
    cfg: OvertimeConfig,
    *,
    overtime_evidence: bool | None = None,
) -> TriggerDecision:
    """评估当前是否应该触发点餐。

    Args:
        now: 当前时间（由 MCP ``now-time-info`` 提供，避免 LLM 猜错时区）
        state: 幂等状态
        cfg: 运行配置
        overtime_evidence: 加班证据。None 表示"无证据可用"，
            此时**不会**自动下单，只会降级为确认——宁可不点，也不误点。
    """
    hour = now.hour
    window = _window_of(hour)

    # ---- 1. 时间窗口 ----
    if not (cfg.trigger_start_hour <= hour < cfg.trigger_end_hour):
        return TriggerDecision(
            Action.SKIP,
            f"当前 {hour:02d}:{now.minute:02d} 不在加班晚餐触发窗口 "
            f"[{cfg.trigger_start_hour:02d}:00, {cfg.trigger_end_hour:02d}:00) 内",
            window,
        )

    # ---- 2. 免打扰时段 ----
    if cfg.quiet_hours:
        lo, hi = cfg.quiet_hours
        in_quiet = (lo <= hour < hi) if lo < hi else (hour >= lo or hour < hi)
        if in_quiet:
            return TriggerDecision(Action.SILENT, f"{hour:02d}:00 处于免打扰时段 {lo}-{hi}", window)

    # ---- 3. 幂等：今天这个窗口已经点过了吗 ----
    key = state.idempotency_key(window)
    if key in state.ordered_windows:
        return TriggerDecision(Action.SKIP, f"{state.date} 的 {window} 已经下过单，不重复触发", window)

    # ---- 4. 每日次数上限 ----
    if state.auto_orders_today >= cfg.max_auto_orders_per_day:
        return TriggerDecision(
            Action.SKIP,
            f"今日自动下单已达上限 {cfg.max_auto_orders_per_day} 次",
            window,
        )

    # ---- 5. 加班证据 ----
    # 没有加班证据就不要自动下单。这是自动付钱场景的基本安全边界。
    if overtime_evidence is not True:
        hint = "未获取到加班信号" if overtime_evidence is None else "检测到当前不在加班状态"
        return TriggerDecision(Action.CONFIRM, f"{hint}，需人工确认后才下单", window)

    # ---- 6. 过了自动下单截止时间 ----
    if hour >= cfg.auto_order_deadline_hour:
        return TriggerDecision(
            Action.CONFIRM,
            f"已过自动下单截止时间 {cfg.auto_order_deadline_hour:02d}:00，转为人工确认",
            window,
        )

    # ---- 7. 全部通过 ----
    if not cfg.auto_order:
        return TriggerDecision(Action.CONFIRM, "配置为不自动下单，需人工确认", window)

    return TriggerDecision(
        Action.ORDER,
        f"{hour:02d}:{now.minute:02d} 命中加班晚餐窗口且确认在加班，可自动下单",
        window,
    )