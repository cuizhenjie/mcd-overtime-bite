"""寻优器测试：核心是「预算硬约束永不被突破」。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from overtime_bite.config import TasteProfile  # noqa: E402
from overtime_bite.models import Category, Channel, Coupon, MenuItem, Store  # noqa: E402
from overtime_bite.optimizer import optimize  # noqa: E402

BUDGET = 3000  # 30 元

MENU = [
    MenuItem("C01", "巨无霸套餐", Category.COMBO, 3900, frozenset({"beef"})),
    MenuItem("C03", "麦辣鸡腿堡套餐", Category.COMBO, 3300),
    MenuItem("B01", "吉士汉堡", Category.BURGER, 1800, frozenset({"beef"})),
    MenuItem("B03", "香辣鸡腿堡", Category.BURGER, 1700),
    MenuItem("S01", "中薯条", Category.SIDE, 700),
    MenuItem("D01", "中可乐", Category.DRINK, 700),
    MenuItem("D03", "燕麦拿铁", Category.DRINK, 1500),
    MenuItem("T01", "蛋挞", Category.DESSERT, 800),
]

COUPONS = [
    Coupon("CP01", "满30减6", threshold_cents=3000, discount_cents=600),
    Coupon("CP02", "满20减3", threshold_cents=2000, discount_cents=300),
]

STORE = Store(store_id="S1", name="望京店", distance_meters=420, walk_minutes=5,
              delivery_fee_cents=600, eta_minutes=32)


def _run(**kw):
    return optimize(
        MENU, COUPONS, budget_cents=BUDGET, profile=TasteProfile(),
        store=STORE, channel=kw.pop("channel", Channel.DELIVERY), **kw
    )


def test_never_exceeds_budget():
    """无论菜单和券怎么变，返回的每个方案都必须 ≤ 预算。"""
    for c in _run():
        assert c.total_cents <= BUDGET, f"{c.describe()} 超出预算"


def test_returns_results():
    assert len(_run()) > 0


def test_sorted_by_score_desc():
    cands = _run()
    scores = [c.score for c in cands]
    assert scores == sorted(scores, reverse=True)


def test_pickup_has_no_delivery_fee():
    """到店取餐不应产生配送费——这是渠道决策的核心收益。"""
    cands = _run(channel=Channel.PICKUP)
    assert all(c.fee_cents == 0 for c in cands)


def test_delivery_includes_fee():
    cands = _run(channel=Channel.DELIVERY)
    assert all(c.fee_cents == 600 for c in cands)


def test_avoid_items_are_excluded():
    """忌口是硬约束，不允许出现在任何候选里。"""
    profile = TasteProfile(avoid_keywords={"汉堡"})
    cands = optimize(MENU, COUPONS, BUDGET, profile, store=STORE, channel=Channel.PICKUP)
    for c in cands:
        assert not any("汉堡" in i.name for i in c.items), c.describe()


def test_uses_coupons_to_fit_budget():
    """33 元套餐 + 6 元配送 = 39 超预算；叠券后应进入可行解。"""
    profile = TasteProfile(avoid_categories={"burger", "chicken"}, prefer_tags=set())
    cands = optimize(MENU, COUPONS, BUDGET, profile, store=STORE,
                     channel=Channel.DELIVERY, max_items=1)
    assert cands, "叠券后应有可行解"
    assert all(c.total_cents <= BUDGET for c in cands)


def test_every_candidate_has_reasons():
    """可解释性是硬要求：每个方案都要能回答"为什么是它"。"""
    for c in _run():
        assert len(c.reasons) > 0, c.describe()


def test_tight_budget_returns_empty_not_overspend():
    """找不到可行解时返回空列表，绝不为凑数而超预算。"""
    cands = optimize(MENU, [], budget_cents=100, profile=TasteProfile(), store=STORE)
    assert cands == []


def test_zero_spicy_tolerance_filters_spicy():
    profile = TasteProfile(spicy_tolerance=0)
    spicy = [MenuItem("B03", "香辣鸡腿堡", Category.BURGER, 1700, frozenset({"spicy"})),
             MenuItem("B01", "吉士汉堡", Category.BURGER, 1800)]
    cands = optimize(spicy, [], BUDGET, profile, store=STORE, channel=Channel.PICKUP)
    for c in cands:
        assert not any("香辣" in i.name for i in c.items)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  PASS  {name}")
    print(f"\n{passed} passed")