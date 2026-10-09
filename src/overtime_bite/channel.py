"""渠道决策：外送还是到店自取？

这是 OvertimeBite 的第二个差异点，也是很多"省钱点餐"项目忽略的地方。

在 30 元预算下，外送配送费（通常 5~9 元）会吃掉 **20%~30%** 的预算。
一家 28 元的外送套餐，如果楼下 6 分钟有门店可自取，实际只花 28 元且更快到手；
反过来，一家 22 元的套餐外送要 29 元，不如多花 6 元买 28 元自取的。

所以真正的"价格最优"必须**先选渠道，再选商品**，而不是反过来。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Channel, Store


@dataclass(frozen=True)
class ChannelDecision:
    channel: Channel
    reason: str
    saved_cents: int = 0


def choose(
    store: Store,
    *,
    budget_cents: int,
    pickup_max_walk_meters: int = 800,
    prefer_delivery: bool = False,
    eating_at_desk: bool = False,
) -> ChannelDecision:
    """在预算约束下选择取餐渠道。

    Args:
        store: 目标门店
        budget_cents: 预算上限（分）
        pickup_max_walk_meters: 愿意步行的最大距离
        prefer_delivery: 用户显式偏好外送
        eating_at_desk: 是否在工位吃——这会改变配送费的心理阈值
            （在工位吃，等 30 分钟到店取反而是折腾）
    """
    can_pickup = store.supports_pickup and store.distance_meters <= pickup_max_walk_meters
    can_delivery = store.supports_delivery and store.delivery_fee_cents > 0

    # 显式偏好优先，但只在用户体验明显受损时才覆盖
    if prefer_delivery:
        if can_delivery:
            return ChannelDecision(
                Channel.DELIVERY,
                f"你偏好外送，门店配送费 ¥{store.delivery_fee_cents / 100:.2f}",
            )
        return ChannelDecision(Channel.DELIVERY, "门店仅支持外送")

    # 在工位吃：等外送是常态，自取的步行+排队是额外成本
    if eating_at_desk and can_delivery:
        return ChannelDecision(
            Channel.DELIVERY,
            f"在工位用餐，外送约 {store.eta_minutes} 分钟送达，避免取餐往返",
        )

    if can_pickup and not can_delivery:
        return ChannelDecision(
            Channel.PICKUP,
            f"门店不支持外送，步行 {store.walk_minutes} 分钟到店取餐",
        )

    if can_pickup and can_delivery:
        delivery_fee = store.delivery_fee_cents
        fee_ratio = delivery_fee / budget_cents if budget_cents else 0
        if fee_ratio >= 0.2:
            return ChannelDecision(
                Channel.PICKUP,
                f"配送费 ¥{delivery_fee / 100:.2f} 已占预算 {fee_ratio:.0%}，"
                f"改为步行 {store.walk_minutes} 分钟自取更划算",
                saved_cents=delivery_fee,
            )
        return ChannelDecision(
            Channel.DELIVERY,
            f"配送费仅占预算 {fee_ratio:.0%}，外送 {store.eta_minutes} 分钟更省事",
        )

    if can_delivery:
        return ChannelDecision(Channel.DELIVERY, f"外送配送费 ¥{store.delivery_fee_cents / 100:.2f}")

    return ChannelDecision(Channel.PICKUP, "默认到店取餐")


def channel_floor_cents(channel: Channel) -> int:
    """渠道带来的最低成本估算，用于两渠道联合寻优时的下界剪枝。"""
    return 0