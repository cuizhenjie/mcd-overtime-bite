"""渠道决策 + 画像学习 + 端到端管线测试。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from overtime_bite import OvertimeBite, OvertimeConfig, TasteProfile, TriggerState  # noqa: E402
from overtime_bite.channel import choose  # noqa: E402
from overtime_bite.mock import MockProvider  # noqa: E402
from overtime_bite.models import Channel, Store  # noqa: E402
from overtime_bite.profile import learn_from_orders  # noqa: E402
from overtime_bite.trigger import Action  # noqa: E402

BUDGET = 3000


def test_pickup_when_delivery_fee_dominates():
    """30 元预算下 6 元配送费占 20%，应改走自取。"""
    store = Store("S1", "望京店", 420, 5, delivery_fee_cents=600, eta_minutes=32)
    d = choose(store, budget_cents=BUDGET, eating_at_desk=False)
    assert d.channel is Channel.PICKUP
    assert d.saved_cents == 600


def test_delivery_when_fee_is_small():
    store = Store("S1", "望京店", 3000, 35, delivery_fee_cents=200, eta_minutes=30)
    d = choose(store, budget_cents=BUDGET, eating_at_desk=False)
    assert d.channel is Channel.DELIVERY


def test_eating_at_desk_prefers_delivery():
    """在工位吃，等 30 分钟外送是常态，自取反而是折腾。"""
    store = Store("S1", "望京店", 420, 5, delivery_fee_cents=600, eta_minutes=32)
    d = choose(store, budget_cents=BUDGET, eating_at_desk=True)
    assert d.channel is Channel.DELIVERY


def test_too_far_falls_back_to_delivery():
    store = Store("S1", "远处店", 5000, 60, delivery_fee_cents=600, eta_minutes=40)
    d = choose(store, budget_cents=BUDGET, eating_at_desk=False)
    assert d.channel is Channel.DELIVERY


def test_prefer_delivery_overrides():
    store = Store("S1", "望京店", 420, 5, delivery_fee_cents=600, eta_minutes=32)
    d = choose(store, budget_cents=BUDGET, prefer_delivery=True, eating_at_desk=False)
    assert d.channel is Channel.DELIVERY


def test_learn_from_orders_builds_weights():
    orders = [
        {"items": [{"name": "吉士汉堡", "category": "burger"},
                   {"name": "中薯条", "category": "side"},
                   {"name": "中可乐", "category": "drink"}]},
        {"items": [{"name": "巨无霸", "category": "burger"},
                   {"name": "中薯条", "category": "side"},
                   {"name": "中可乐", "category": "drink"}]},
        {"items": [{"name": "吉士汉堡", "category": "burger"},
                   {"name": "中可乐", "category": "drink"}]},
    ]
    p = learn_from_orders(orders)
    # 点得最多的品类权重最高
    assert p.likes["burger"] > p.likes["side"]
    assert p.need_drink is True


def test_unobserved_category_stays_neutral():
    """无观测证据的品类保持中性 0.5，而不是被凭空打成 0。

    「没点过」≠「讨厌」。把它当讨厌会让画像快速固化，越用越窄。
    """
    orders = [
        {"items": [{"name": "吉士汉堡", "category": "burger"},
                   {"name": "中可乐", "category": "drink"}]} for _ in range(4)
    ]
    p = learn_from_orders(orders)
    assert p.category_weight("dessert") == 0.5
    assert "dessert" not in p.likes


def test_observed_weight_is_shrunk_toward_neutral():
    """少量样本不应把权重推向 0/1 极端——样本少就保持保守。"""
    orders = [{"items": [{"name": "吉士汉堡", "category": "burger"}]} for _ in range(4)]
    p = learn_from_orders(orders)
    # 4/4 全是汉堡，但因先验收缩，权重仍应明显低于 1.0
    assert 0.5 < p.likes["burger"] < 1.0


def test_learn_cold_start_does_not_invent_preferences():
    """样本不足时不应给出偏好结论——宁可不学，也不要学错。"""
    p = learn_from_orders([{"items": [{"name": "吉士汉堡", "category": "burger"}]}])
    assert p.likes == {}


def test_end_to_end_produces_order_ready_plan():
    provider = MockProvider(now=datetime(2026, 10, 9, 19, 30))
    cfg = OvertimeConfig(budget_cap_cents=BUDGET)
    d = OvertimeBite(provider, cfg).run(
        now=datetime(2026, 10, 9, 19, 30),
        state=TriggerState(date="2026-10-09"),
        overtime_evidence=True,
        dry_run=True,
    )
    assert d.action is Action.ORDER
    assert d.chosen is not None
    assert d.chosen.within_budget(BUDGET)
    assert "create-order" not in provider.calls, "演练模式不应真实下单"


def test_end_to_end_binds_coupons_first():
    """一键领券是收益最高、风险最低的一步，必须在寻优前完成。"""
    provider = MockProvider(now=datetime(2026, 10, 9, 19, 30))
    OvertimeBite(provider, OvertimeConfig(budget_cap_cents=BUDGET)).run(
        now=datetime(2026, 10, 9, 19, 30),
        state=TriggerState(date="2026-10-09"),
        overtime_evidence=True, dry_run=True,
    )
    assert provider.bound_coupons is True
    assert provider.calls.index("auto-bind-coupons") < provider.calls.index("query-meals")


def test_real_order_only_when_not_dry_run():
    provider = MockProvider(now=datetime(2026, 10, 9, 19, 30))
    d = OvertimeBite(provider, OvertimeConfig(budget_cap_cents=BUDGET)).run(
        now=datetime(2026, 10, 9, 19, 30),
        state=TriggerState(date="2026-10-09"),
        overtime_evidence=True, dry_run=False,
    )
    assert "create-order" in provider.calls
    assert d.order_result and d.order_result["orderNo"]


def test_order_failure_degrades_gracefully():
    provider = MockProvider(now=datetime(2026, 10, 9, 19, 30), create_order_raises=True)
    d = OvertimeBite(provider, OvertimeConfig(budget_cap_cents=BUDGET)).run(
        now=datetime(2026, 10, 9, 19, 30),
        state=TriggerState(date="2026-10-09"),
        overtime_evidence=True, dry_run=False,
    )
    assert d.action is Action.CONFIRM
    assert "下单失败" in d.summary


def test_confirm_path_never_orders():
    """无加班证据时，即使 dry_run=False 也绝不能调用 create-order。"""
    provider = MockProvider(now=datetime(2026, 10, 9, 19, 30))
    d = OvertimeBite(provider, OvertimeConfig(budget_cap_cents=BUDGET)).run(
        now=datetime(2026, 10, 9, 19, 30),
        state=TriggerState(date="2026-10-09"),
        overtime_evidence=None, dry_run=False,
    )
    assert d.action is Action.CONFIRM
    assert "create-order" not in provider.calls


def test_office_hours_never_orders():
    provider = MockProvider(now=datetime(2026, 10, 9, 10, 0))
    d = OvertimeBite(provider, OvertimeConfig(budget_cap_cents=BUDGET)).run(
        now=datetime(2026, 10, 9, 10, 0),
        state=TriggerState(date="2026-10-09"),
        overtime_evidence=True, dry_run=False,
    )
    assert d.action is Action.SKIP
    assert "create-order" not in provider.calls


if __name__ == "__main__":
    passed = 0
    failed = []
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