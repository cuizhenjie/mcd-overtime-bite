"""配置与口味画像模型。

设计原则：**所有自动下单行为都受硬约束保护**，配置里没有任何"绕过确认"的开关。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 金额一律使用「分」为单位的整数，避免浮点误差（0.1+0.2 问题）
CENTS = 100


@dataclass
class TasteProfile:
    """用户口味画像。

    这是"越用越准"的部分：既可以手写，也可以由 ``profile.learn_from_orders``
    从 MCP 的 ``order-list`` 历史订单里自动学习出来。
    """

    # 品类偏好权重，0.0 ~ 1.0。缺失品类视为 0.5（中性）
    likes: dict[str, float] = field(default_factory=dict)

    # 明确不吃的品类 / 忌口关键词（匹配商品名做兜底拦截）
    avoid_categories: set[str] = field(default_factory=set)
    avoid_keywords: set[str] = field(default_factory=set)

    # 口味偏好标签，命中商品标签时加分
    prefer_tags: set[str] = field(default_factory=set)

    # 辣度容忍 0~3，超过则过滤辣味商品
    spicy_tolerance: int = 2

    # 是否需要饮料（加班场景默认 True，缺水会犯困）
    need_drink: bool = True

    # 夜宵场景是否接受甜品（小甜点能缓解加班疲劳）
    night_snack_wants_dessert: bool = True

    def category_weight(self, category: str) -> float:
        """品类权重，缺失时返回中性值 0.5。"""
        return float(self.likes.get(category, 0.5))

    def is_blocked(self, item_name: str, category: str) -> bool:
        """硬性拦截：忌口是不可协商的，预算不够可以退，忌口不能退。"""
        if category in self.avoid_categories:
            return True
        name = item_name.strip()
        return any(kw and kw in name for kw in self.avoid_keywords)


@dataclass
class OvertimeConfig:
    """OvertimeBite 的运行配置。"""

    # ---- 触发条件 ----
    # 晚餐触发窗口（24 小时制，左闭右开）。加班晚餐场景默认 18:00 之后
    trigger_start_hour: int = 18
    trigger_end_hour: int = 24

    # 触发后的下单截止时间；超过该时间仍可下单，但强制走"确认"模式
    auto_order_deadline_hour: int = 22

    # ---- 预算硬约束 ----
    # 单笔预算上限（分）。这是**硬约束**，寻优器绝不会返回超过它的方案
    budget_cap_cents: int = 30 * CENTS

    # 允许的最大优惠使用数量（叠券可能触发风控）
    max_coupons: int = 2

    # ---- 渠道决策 ----
    # 到店取餐的最大步行距离（米）。超过则只考虑麦乐送外送
    pickup_max_walk_meters: int = 800

    # 每个订单最多几件商品
    max_items_per_order: int = 4

    # ---- 自动化安全 ----
    # 是否允许"无人工确认直接下单"。
    # 注意：即便为 True，也依然受上面所有硬约束保护；任一约束不满足自动降级为"需确认"
    auto_order: bool = True

    # 每日自动下单次数上限
    max_auto_orders_per_day: int = 1

    # 夜间免打扰时间窗内，即使触发也只做静默推荐
    quiet_hours: tuple[int, int] | None = None

    profile: TasteProfile = field(default_factory=TasteProfile)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "OvertimeConfig":
        """从 dict 构造，容忍未知字段（便于向前兼容配置文件）。"""
        data = dict(data or {})
        profile_raw = data.pop("profile", {}) or {}

        likes = {str(k): float(v) for k, v in (profile_raw.get("likes") or {}).items()}
        profile = TasteProfile(
            likes=likes,
            avoid_categories=set(profile_raw.get("avoid_categories") or []),
            avoid_keywords=set(profile_raw.get("avoid_keywords") or []),
            prefer_tags=set(profile_raw.get("prefer_tags") or []),
            spicy_tolerance=int(profile_raw.get("spicy_tolerance", 2)),
            need_drink=bool(profile_raw.get("need_drink", True)),
            night_snack_wants_dessert=bool(profile_raw.get("night_snack_wants_dessert", True)),
        )

        cfg = cls(profile=profile)
        for key, value in data.items():
            if hasattr(cfg, key) and key != "profile":
                current = getattr(cfg, key)
                if isinstance(current, bool):
                    setattr(cfg, key, bool(value))
                elif isinstance(current, int):
                    setattr(cfg, key, int(value))
                elif isinstance(current, tuple) or key == "quiet_hours":
                    setattr(cfg, key, tuple(value) if value else None)
                else:
                    setattr(cfg, key, value)
        return cfg