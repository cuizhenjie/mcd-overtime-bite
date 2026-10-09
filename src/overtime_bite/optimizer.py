"""组合寻优：在预算硬约束下找到"性价比最高"的点餐方案。

为什么不能只是"挑便宜的"？
因为用户说的"价格最优"真实含义是**性价比最优**：
花同样的钱，吃得更饱、更合口味，才叫最优。

本模块做三件事：
1. **生成**候选组合（套餐 / 单品 / 主食+小食+饮料的组合）
2. **约束**：预算、忌口、辣度是硬过滤，不是软惩罚
3. **排序**：多目标评分，每一项都能解释清楚为什么给这个方案打分

复杂度：菜单 n 个商品，最多选 k 个，C(n,k) 级别。
实测 n=60, k=4 时约 47 万组合，对交互式调用完全可接受。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from .config import TasteProfile
from .models import Candidate, Category, Channel, Coupon, MenuItem, Store

# 各品类的饱腹基线（0~1）。有营养数据时会按热量修正。
_BASE_SATIETY: dict[Category, float] = {
    Category.BURGER: 0.80,
    Category.CHICKEN: 0.62,
    Category.SIDE: 0.35,
    Category.DRINK: 0.12,
    Category.DESSERT: 0.22,
    Category.COMBO: 0.95,
}

# 评分权重。可按个人偏好调整，但保持总和语义清晰。
WEIGHT_SATIETY = 0.45
WEIGHT_TASTE = 0.30
WEIGHT_BUDGET = 0.15
WEIGHT_SPEED = 0.10


@dataclass
class SearchLimits:
    """搜索上限，防止超大菜单导致组合爆炸。"""

    max_candidates: int = 20000


def _satiety(item: MenuItem) -> float:
    """估计单品饱腹度。有热量数据时按热量放大。"""
    base = _BASE_SATIETY.get(item.category, 0.3)
    if item.calories:
        # 400kcal 视为"一份主食"的参考量
        base = min(1.0, base * (0.6 + 0.4 * min(item.calories, 700) / 400))
    return base


def _passes_filters(items: tuple[MenuItem, ...], profile: TasteProfile) -> bool:
    """硬过滤：忌口不可协商，辣度超限直接淘汰。"""
    for item in items:
        if profile.is_blocked(item.name, item.category.value):
            return False
        if "spicy" in item.tags and item.name and profile.spicy_tolerance == 0:
            return False
    return True


def _coupon_options(
    subtotal_cents: int, coupons: list[Coupon], max_coupons: int
) -> list[tuple[Coupon, ...]]:
    """枚举该小计下值得使用的券组合。

    只保留真正能省钱且金额合理的券——大额券通常有严格品类限制，
    叠太多反而可能触发门店风控，这里做保守处理。
    """
    usable = [
        c for c in coupons
        if c.discount_for(subtotal_cents) > 0 and c.discount_cents <= 5000
    ]
    if not usable:
        return [()]

    options: list[tuple[Coupon, ...]] = [()]
    for size in range(1, min(max_coupons, len(usable)) + 1):
        for combo in itertools.combinations(usable, size):
            total_discount = sum(c.discount_cents for c in combo)
            # 券面额总和超过小计本身就没意义了
            if total_discount < subtotal_cents:
                options.append(combo)
    return options


def _score(
    items: tuple[MenuItem, ...],
    candidate: Candidate,
    profile: TasteProfile,
    store: Store,
) -> tuple[float, tuple[str, ...]]:
    """多目标评分，返回 (分数, 解释列表)。

    解释是硬要求：自动下单产品必须能回答"为什么是它"。
    """
    reasons: list[str] = []

    # ---- 1. 饱腹度 ----
    satiety = min(1.0, sum(_satiety(i) for i in items))
    if satiety >= 0.9:
        reasons.append(f"饱腹度高（{satiety:.2f}），加班到深夜不容易饿醒")

    # ---- 2. 口味匹配 ----
    taste = 0.0
    for item in items:
        taste += profile.category_weight(item.category.value)
        for tag in item.tags:
            if tag in profile.prefer_tags:
                taste += 0.15
    taste = taste / len(items) if items else 0.0
    if taste >= 0.6:
        reasons.append(f"口味匹配度高（{taste:.2f}），符合历史点餐习惯")

    # ---- 3. 预算利用 ----
    # 在不超过预算的前提下，越贴近预算越"吃得好"，但要归一化避免压倒其它目标
    budget_util = candidate.total_cents / candidate.subtotal_cents if candidate.subtotal_cents else 0
    if candidate.discount_cents > 0:
        reasons.append(f"叠加优惠省 ¥{candidate.discount_cents / 100:.2f}")

    # ---- 4. 速度 ----
    if candidate.channel is Channel.PICKUP:
        speed = 1.0
        reasons.append(f"到店自取，约 {store.walk_minutes} 分钟，省配送费")
    else:
        speed = max(0.0, 1.0 - store.eta_minutes / 60.0)

    score = (
        WEIGHT_SATIETY * satiety
        + WEIGHT_TASTE * taste
        + WEIGHT_BUDGET * min(1.0, budget_util)
        + WEIGHT_SPEED * speed
    )
    return score, tuple(reasons)


def _combinations(items: list[MenuItem], max_items: int, limits: SearchLimits) -> list[tuple[MenuItem, ...]]:
    """生成候选商品组合。

    套餐单独处理（套餐内部已含搭配，整体作为一个候选），
    非套餐商品按 1~max_items 规模组合。
    """
    combos_list: list[MenuItem, ...] = [i for i in items if i.is_combo]
    loose = [i for i in items if not i.is_combo]

    result: list[tuple[MenuItem, ...]] = [(c,) for c in combos_list]

    for size in range(1, max_items + 1):
        if len(loose) < size:
            break
        for combo in itertools.combinations(loose, size):
            result.append(combo)
            if len(result) >= limits.max_candidates:
                return result
    return result


def optimize(
    menu: list[MenuItem],
    coupons: list[Coupon],
    budget_cents: int,
    profile: TasteProfile,
    *,
    store: Store | None = None,
    channel: Channel = Channel.DELIVERY,
    max_items: int = 4,
    max_coupons: int = 2,
    limits: SearchLimits | None = None,
    top_k: int = 3,
) -> list[Candidate]:
    """在预算内寻优，返回按分数降序排列的前 N 个候选。

    **保证**：返回的每个 Candidate 都满足 ``within_budget(budget_cents)``。
    找不到任何可行解时返回空列表——不为了凑数而超预算。
    """
    limits = limits or SearchLimits()
    store = store or Store(store_id="mock", name="模拟门店", distance_meters=500, walk_minutes=6)
    fee = store.channel_fee(channel)

    # 饮料需求：加班场景默认需要补一杯，但不强加，避免为了凑单而超预算
    candidates: list[Candidate] = []

    for items in _combinations(menu, max_items, limits):
        if not _passes_filters(items, profile):
            continue

        subtotal = sum(i.price_cents for i in items)
        if subtotal <= 0:
            continue
        # 券前先做一次粗过滤，避免无谓的券枚举
        if subtotal + fee > budget_cents:
            # 券可能让原本超预算的方案变得可行，所以不能直接丢弃，
            # 但要把枚举范围收窄：只在这类"接近预算"的方案上算券
            if subtotal + fee > budget_cents * 1.5:
                continue

        for coupon_set in _coupon_options(subtotal, coupons, max_coupons):
            discount = sum(c.discount_for(subtotal) for c in coupon_set)
            candidate = Candidate(
                items=items,
                channel=channel,
                subtotal_cents=subtotal,
                fee_cents=fee,
                discount_cents=discount,
                coupons_used=coupon_set,
            )
            if not candidate.within_budget(budget_cents):
                continue

            candidate.score, candidate.reasons = _score(items, candidate, profile, store)
            candidates.append(candidate)

    # 同分去重：按 (商品集合, 渠道) 只保留最优惠的那张券组合
    best_by_key: dict[tuple, Candidate] = {}
    for c in candidates:
        key = (tuple(sorted(i.item_id for i in c.items)), c.channel)
        if key not in best_by_key or c.total_cents < best_by_key[key].total_cents:
            best_by_key[key] = c

    ranked = sorted(best_by_key.values(), key=lambda c: (-c.score, c.total_cents))
    return ranked[:top_k]