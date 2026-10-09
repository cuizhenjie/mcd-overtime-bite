"""核心领域模型：菜单商品、优惠券、候选方案、门店。

所有金额单位为「分」。这些模型是**纯数据**，不含 MCP 依赖，
因此可以在没有 Token 的情况下被完整测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    BURGER = "burger"          # 汉堡 / 主食
    CHICKEN = "chicken"        # 鸡类
    SIDE = "side"              # 小食
    DRINK = "drink"            # 饮料
    DESSERT = "dessert"        # 甜品
    COMBO = "combo"            # 套餐（含主食+小食+饮料）


class Channel(str, Enum):
    DELIVERY = "delivery"      # 麦乐送外送
    PICKUP = "pickup"          # 到店取餐 / 来道取


@dataclass(frozen=True)
class MenuItem:
    """一个可售卖的餐品。字段与麦当劳 MCP ``query-meals`` 返回对齐。"""

    item_id: str
    name: str
    category: Category
    price_cents: int
    tags: frozenset[str] = frozenset()

    # 套餐包含的商品 id；仅 category == COMBO 时有意义
    includes: tuple[str, ...] = ()

    # 营养信息（来自 list-nutrition-foods），可选
    calories: float | None = None
    protein_g: float | None = None

    @property
    def is_combo(self) -> bool:
        return self.category is Category.COMBO

    def __post_init__(self) -> None:
        if self.price_cents < 0:
            raise ValueError(f"price_cents 不能为负：{self.name}")


@dataclass(frozen=True)
class Coupon:
    """优惠券。区分「折扣」与「满减」两类。"""

    coupon_id: str
    title: str
    threshold_cents: int = 0       # 满减门槛，0 表示无门槛
    discount_cents: int = 0        # 直减金额
    scope: str = "all"             # all / category / item

    def discount_for(self, subtotal_cents: int) -> int:
        """计算这张券能省多少，永远不会返回超过商品金额的值。"""
        if subtotal_cents < self.threshold_cents:
            return 0
        return min(self.discount_cents, subtotal_cents)

    @property
    def is_stackable(self) -> bool:
        return self.discount_cents <= 3000  # 30 元以内的小额券才允许叠加


@dataclass
class Store:
    """门店信息（来自 ``query-nearby-stores`` / ``delivery-query-stores``）。"""

    store_id: str
    name: str
    distance_meters: int
    walk_minutes: int = 0
    supports_pickup: bool = True
    supports_delivery: bool = True
    delivery_fee_cents: int = 0
    eta_minutes: int = 30

    def channel_fee(self, channel: Channel) -> int:
        if channel is Channel.DELIVERY:
            return self.delivery_fee_cents
        return 0


@dataclass
class Candidate:
    """一个候选点餐方案。"""

    items: tuple[MenuItem, ...]
    channel: Channel
    subtotal_cents: int
    fee_cents: int
    discount_cents: int
    coupons_used: tuple[Coupon, ...]
    score: float = 0.0
    reasons: tuple[str, ...] = ()
    # 由 MCP calculate-price 返回的权威应付金额。
    # 本地估算只用于"生成候选"，最终金额以麦当劳侧为准——两者不一致时以这里为准。
    verified_total_cents: int | None = None

    @property
    def total_cents(self) -> int:
        """应付总价 = 商品小计 + 渠道费用 - 优惠。有权威复核值时以复核值为准。"""
        if self.verified_total_cents is not None:
            return self.verified_total_cents
        return max(0, self.subtotal_cents + self.fee_cents - self.discount_cents)

    def within_budget(self, budget_cents: int) -> bool:
        return self.total_cents <= budget_cents

    def money(self) -> str:
        return f"¥{self.total_cents / 100:.2f}"

    def describe(self) -> str:
        names = " + ".join(i.name for i in self.items)
        ch = "麦乐送外送" if self.channel is Channel.DELIVERY else "到店取餐"
        fee = f"配送费¥{self.fee_cents / 100:.2f}" if self.fee_cents else "免配送费"
        save = f"，已用券省¥{self.discount_cents / 100:.2f}" if self.discount_cents else ""
        return f"{names}｜{ch}（{fee}{save}）｜合计 {self.money()}"


@dataclass
class TriggerState:
    """幂等状态：防止同一天重复下单。"""

    date: str                                    # YYYY-MM-DD
    auto_orders_today: int = 0
    ordered_windows: set[str] = field(default_factory=set)  # 已下单的时间窗标识

    def idempotency_key(self, window: str) -> str:
        return f"{self.date}#{window}"