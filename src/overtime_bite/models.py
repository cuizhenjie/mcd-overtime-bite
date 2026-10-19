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
    """一个可售卖的餐品。字段与麦当劳 MCP 的返回对齐。

    ``original_price_cents`` 用于展示"原价 → 券后价"，因为门店券的定价就藏在
    券标题里（见 :func:`overtime_bite.pipeline._to_menu`）。留空表示无原价对比。
    """

    item_id: str
    name: str
    category: Category
    price_cents: int
    tags: frozenset[str] = frozenset()
    # 命中门店券时的**原价**；``price_cents`` 此时已是券后价
    original_price_cents: int | None = None

    # 套餐包含的商品 id；仅 category == COMBO 时有意义
    includes: tuple[str, ...] = ()

    # 营养信息（来自 list-nutrition-foods），可选
    calories: float | None = None
    protein_g: float | None = None

    @property
    def is_combo(self) -> bool:
        return self.category is Category.COMBO

    @property
    def has_coupon_price(self) -> bool:
        return self.original_price_cents is not None and self.original_price_cents > self.price_cents

    def __post_init__(self) -> None:
        if self.price_cents < 0:
            raise ValueError(f"price_cents 不能为负：{self.name}")


@dataclass(frozen=True)
class Coupon:
    """优惠券。

    ⚠️ 真实麦当劳券以**商品型**为主（如「中薯条买一送一」），只绑定一个
    ``productCode``，**没有"满 X 减 Y"的金额结构**。因此：

      - ``discount_cents`` 未知时必须留 0，**绝不能臆造优惠金额**——
        凭空算出的"省 ¥10"会让展示金额与实际扣款不符
      - 券的真实作用是「让含该商品的篮子更划算」，由 ``calculate-price`` 裁决
    """

    coupon_id: str
    title: str
    threshold_cents: int = 0       # 满减门槛，0 表示无门槛（商品型券通常为 0）
    discount_cents: int = 0        # 直减金额；未知时为 0
    scope: str = "all"             # all / product
    # 适用的商品编码集合；商品型券据此生效
    product_codes: tuple[str, ...] = ()

    @property
    def is_product_coupon(self) -> bool:
        """商品型券：有明确的适用商品，但没有可计算的优惠金额。"""
        return bool(self.product_codes) and self.discount_cents == 0

    def applies_to(self, item_ids: set[str]) -> bool:
        """篮子中是否含有该券的适用商品。"""
        return bool(self.product_codes) and bool(self.product_codes_set() & item_ids)

    def product_codes_set(self) -> frozenset[str]:
        return frozenset(self.product_codes)

    def discount_for(self, subtotal_cents: int) -> int:
        """可计算的直减金额。商品型券返回 0——金额由 calculate-price 裁决。"""
        if self.is_product_coupon:
            return 0
        if subtotal_cents < self.threshold_cents:
            return 0
        return min(self.discount_cents, subtotal_cents)

    @property
    def is_stackable(self) -> bool:
        return self.discount_cents <= 3000  # 30 元以内的小额券才允许叠加


@dataclass
class Store:
    """门店信息（来自 ``query-nearby-stores`` / ``delivery-query-stores``）。

    ``store_code`` 与 ``be_code`` 是麦当劳侧的入参配对关系，不是装饰：
    查询菜单/券时两者要一起传；到店自取场景 ``be_code`` 必须为空，传了会报错。
    """

    store_id: str
    name: str
    distance_meters: int
    walk_minutes: int = 0
    supports_pickup: bool = True
    supports_delivery: bool = True
    delivery_fee_cents: int = 0
    eta_minutes: int = 30
    # 业务类型：1 到店自提 / 2 麦乐送 / 5 得来速 / 6 团餐
    be_type: int = 1
    be_code: str = ""

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
        parts = []
        for i in self.items:
            if i.has_coupon_price:
                parts.append(
                    f"{i.name}(原价¥{i.original_price_cents / 100:.1f}→券后¥{i.price_cents / 100:.1f})"
                )
            else:
                parts.append(i.name)
        names = " + ".join(parts)
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