#!/usr/bin/env python3
"""OvertimeBite 端到端演示（离线，无需 Token）。

演示四个真实场景：
1. 19:30 加班中· 自动触发并给出最优方案
2. 15:00 下午     · 不在晚餐窗口，不打扰
3. 21:30 深夜     · 过自动下单截止时间，转人工确认
4. 同一时间窗重复 · 幂等，不重复下单

运行：python examples/run_demo.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from overtime_bite import OvertimeBite, OvertimeConfig, TriggerState  # noqa: E402
from overtime_bite.mock import MockProvider  # noqa: E402


def show(title: str, decision, provider: MockProvider) -> None:
    print("=" * 68)
    print(title)
    print("=" * 68)
    for line in decision.summary.splitlines():
        print("  " + line)
    if decision.chosen:
        print(f"\n  评分明细：{decision.chosen.score:.3f}")
        for i, r in enumerate(decision.candidates, 1):
            mark = "← 选中" if i == 1 else ""
            print(f"    备选{i}: {i2str(r)} {mark}")
    print("\n  执行轨迹：")
    for step in decision.verbose_trace:
        print(f"    · {step}")
    print(f"\n  MCP 调用序列：{' → '.join(provider.calls)}")
    print()


def i2str(c) -> str:
    return f"{c.describe()}  评分{c.score:.3f}"


def main() -> None:
    cfg = OvertimeConfig(budget_cap_cents=3000, auto_order=True)

    scenarios = [
        ("场景 1｜19:30 加班中，自动触发", datetime(2026, 10, 9, 19, 30), True, False),
        ("场景 2｜15:00 下午茶时间，不该打扰", datetime(2026, 10, 9, 15, 0), True, False),
        ("场景 3｜21:30 深夜加班，需人工确认", datetime(2026, 10, 9, 21, 30), True, False),
        ("场景 4｜无加班证据，降级为确认", datetime(2026, 10, 9, 19, 45), None, False),
    ]

    for title, now, evidence, _ in scenarios:
        provider = MockProvider(now=now)
        bot = OvertimeBite(provider, cfg)
        state = TriggerState(date=f"{now:%Y-%m-%d}")
        decision = bot.run(now=now, state=state, overtime_evidence=evidence, dry_run=True)
        show(title, decision, provider)

    # 幂等演示：同一时间窗跑两次
    print("=" * 68)
    print("场景 5｜幂等：同一时间窗重复触发")
    print("=" * 68)
    now = datetime(2026, 10, 9, 19, 30)
    state = TriggerState(date="2026-10-09", ordered_windows={"2026-10-09#dinner-16"})
    provider = MockProvider(now=now)
    decision = OvertimeBite(provider, cfg).run(
        now=now, state=state, overtime_evidence=True, dry_run=True
    )
    print(f"  动作：{decision.action.value}")
    print(f"  原因：{decision.summary}")
    print()

    print("提示：配置 export MCD_MCP_TOKEN=<token> 后，")
    print("     可把 MockProvider 换成 McdDataProvider 走真实麦当劳 MCP。")


if __name__ == "__main__":
    main()