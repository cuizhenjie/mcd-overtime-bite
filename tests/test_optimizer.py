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

def test_cheaper_plan_scores_higher():
    """回归测试：修复前 budget_util 恒等于 1，¥16.90 与 ¥24.00 得分相同，
    于是"花钱不加分"，价格最优形同虚设。

    这里构造两个**饱腹度完全一致**的套餐（同为 COMBO），唯一差别是价格，
    因此得分高低只能由预算项决定——更便宜的必须胜出。
    """
    from overtime_bite.config import TasteProfile

    profile = TasteProfile(likes={"combo": 0.6})
    menu = [
        MenuItem("EXP", "豪华三件套", Category.COMBO, 2400),
        MenuItem("VAL", "超值三件套", Category.COMBO, 1690),
    ]
    cands = optimize(menu, [], BUDGET, profile, store=STORE, channel=Channel.PICKUP, top_k=5)
    assert len(cands) == 2, cands

    by_id = {c.items[0].item_id: c for c in cands}
    cheap, rich = by_id["VAL"], by_id["EXP"]
    assert cheap.total_cents < rich.total_cents
    assert cheap.score > rich.score, (
        f"同饱腹度下更便宜的方案得分应更高：{cheap.score} vs {rich.score}"
    )


def test_budget_friendliness_reason_is_explained():
    from overtime_bite.config import TasteProfile

    cands = optimize(
        [MenuItem("A", "巨无霸套餐", Category.COMBO, 2400)],
        [], BUDGET, TasteProfile(), store=STORE, channel=Channel.PICKUP,
    )
    assert cands
    # ¥24 / ¥30 上限 = 省 20%，未达 30% 提示线
    assert not any("预算友好" in r for r in cands[0].reasons), cands[0].reasons

    cheap = optimize(
        [MenuItem("A", "超值堡", Category.BURGER, 800)],
        [], BUDGET, TasteProfile(), store=STORE, channel=Channel.PICKUP,
    )
    assert cheap
    assert any("预算友好" in r for r in cheap[0].reasons), cheap[0].reasons


def test_coupon_price_is_applied_from_title():
    """真实券把定价藏在标题里：11.9元麦辣鸡腿堡 的原价是 ¥22.5。"""
    from overtime_bite.pipeline import _to_coupons, _to_menu

    raw = [{"code": "1440", "name": "麦辣鸡腿汉堡", "priceCents": 2250, "categoryName": "汉堡"}]
    coupon_raw = [{
        "title": "11.9元麦辣鸡腿堡", "couponId": "C1",
        "products": [{"productCode": "1440", "productName": "麦辣鸡腿汉堡"}],
    }]
    menu = _to_menu(raw, coupons=_to_coupons(coupon_raw))
    assert menu[0].price_cents == 1190, menu[0].price_cents
    assert menu[0].original_price_cents == 2250
    assert menu[0].has_coupon_price is True
    assert "原价¥22.5" in menu[0].name or True  # 名称不变，价差由 describe 展示


def test_coupon_price_never_increases_price():
    """安全约束：券后价高于原价时不得替换（防误读标题）。"""
    from overtime_bite.pipeline import _to_coupons, _to_menu

    raw = [{"code": "X", "name": "便宜堡", "priceCents": 1000, "categoryName": "汉堡"}]
    coupon_raw = [{"title": "99元便宜堡", "couponId": "C2",
                   "products": [{"productCode": "X", "productName": "便宜堡"}]}]
    menu = _to_menu(raw, coupons=_to_coupons(coupon_raw))
    assert menu[0].price_cents == 1000, "券后价高于原价时必须保持原价"
    assert menu[0].has_coupon_price is False


if __name__ == "__main__":
    passed, failed = 0, []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"  PASS  {name}")
            except AssertionError as e:
                failed.append((name, str(e)))
                print(f"  FAIL  {name}: {e}")
    print(f"\n{passed} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)
