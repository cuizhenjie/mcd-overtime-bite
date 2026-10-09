"""触发引擎测试：自动下单的安全边界。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from overtime_bite.config import OvertimeConfig  # noqa: E402
from overtime_bite.models import TriggerState  # noqa: E402
from overtime_bite.trigger import Action, evaluate  # noqa: E402

CFG = OvertimeConfig(budget_cap_cents=3000)


def test_in_window_with_evidence_auto_orders():
    d = evaluate(datetime(2026, 10, 9, 19, 30), TriggerState(date="2026-10-09"), CFG, overtime_evidence=True)
    assert d.action is Action.ORDER, d.reason


def test_outside_window_skips():
    d = evaluate(datetime(2026, 10, 9, 15, 0), TriggerState(date="2026-10-09"), CFG, overtime_evidence=True)
    assert d.action is Action.SKIP


def test_no_evidence_requires_confirmation():
    """没有加班证据时，绝不能自动付钱。"""
    d = evaluate(datetime(2026, 10, 9, 19, 30), TriggerState(date="2026-10-09"), CFG, overtime_evidence=None)
    assert d.action is Action.CONFIRM


def test_not_overtime_requires_confirmation():
    d = evaluate(datetime(2026, 10, 9, 19, 30), TriggerState(date="2026-10-09"), CFG, overtime_evidence=False)
    assert d.action is Action.CONFIRM


def test_idempotent_within_same_window():
    """同一时间窗只允许一次自动下单。"""
    state = TriggerState(date="2026-10-09", ordered_windows={"2026-10-09#dinner-16"})
    d = evaluate(datetime(2026, 10, 9, 19, 30), state, CFG, overtime_evidence=True)
    assert d.action is Action.SKIP
    assert "已经下过单" in d.reason


def test_daily_cap_respected():
    state = TriggerState(date="2026-10-09", auto_orders_today=1)
    d = evaluate(datetime(2026, 10, 9, 19, 30), state, CFG, overtime_evidence=True)
    assert d.action is Action.SKIP


def test_after_deadline_requires_confirmation():
    d = evaluate(datetime(2026, 10, 9, 22, 30), TriggerState(date="2026-10-09"), CFG, overtime_evidence=True)
    assert d.action is Action.CONFIRM


def test_quiet_hours_silent():
    cfg = OvertimeConfig(budget_cap_cents=3000, quiet_hours=(21, 23))
    d = evaluate(datetime(2026, 10, 9, 21, 30), TriggerState(date="2026-10-09"), cfg, overtime_evidence=True)
    assert d.action is Action.SILENT


def test_auto_order_disabled_requires_confirmation():
    cfg = OvertimeConfig(budget_cap_cents=3000, auto_order=False)
    d = evaluate(datetime(2026, 10, 9, 19, 30), TriggerState(date="2026-10-09"), cfg, overtime_evidence=True)
    assert d.action is Action.CONFIRM


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  PASS  {name}")
    print(f"\n{passed} passed")