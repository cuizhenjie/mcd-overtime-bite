"""编排管线：把触发、画像、渠道、寻优串成一次完整的加班晚餐决策。

执行顺序是有讲究的：**先判渠道，再选商品**。
因为 30 元预算下配送费是一个必须先剔除的常数项，
先把常数项算清楚，商品寻优的搜索空间才真实。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from . import channel as channel_mod
from . import optimizer, profile as profile_mod, trigger
from .config import OvertimeConfig
from .models import (
    Candidate,
    Category,
    Channel,
    Coupon,
    MenuItem,
    Store,
    TriggerState,
)


class DataProvider(Protocol):
    """数据来源协议。

    生产环境由 ``McdDataProvider``（真实麦当劳 MCP）实现；
    测试与演示由 ``MockProvider`` 实现。二者行为契约一致。
    """

    def now(self) -> dict[str, Any]: ...
    def my_addresses(self) -> list[dict[str, Any]]: ...
    def nearby_stores(self, address: str | None = None) -> list[dict[str, Any]]: ...
    def meals(self, store_id: str) -> list[dict[str, Any]]: ...
    def store_coupons(self, store_id: str) -> list[dict[str, Any]]: ...
    def bind_all_coupons(self) -> dict[str, Any]: ...
    def order_history(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def calculate_price(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class Decision:
    """一次完整决策的结果，字段可直接用于给用户展示。"""

    action: trigger.Action
    summary: str
    reasons: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    chosen: Candidate | None = None
    channel_decision: channel_mod.ChannelDecision | None = None
    profile_note: str = ""
    order_result: dict[str, Any] | None = None
    verbose_trace: list[str] = field(default_factory=list)


def _to_menu(raw: list[dict[str, Any]], nutrition_index: dict[str, dict] | None = None) -> list[MenuItem]:
    """把 MCP ``query-meals`` 返回转成 MenuItem。"""
    nutrition_index = nutrition_index or {}
    menu: list[MenuItem] = []
    for row in raw:
        name = row.get("name") or row.get("mealName") or ""
        if not name:
            continue
        price_raw = row.get("price") or row.get("priceCents") or 0
        try:
            # 麦当劳 MCP 价格通常为「元」的字符串或数值
            price_cents = int(round(float(price_raw) * 100)) if float(price_raw) < 1000 else int(float(price_raw))
        except (TypeError, ValueError):
            price_cents = 0
        if price_cents <= 0:
            continue

        cat_raw = row.get("category") or profile_mod.guess_category(name).value
        try:
            category = Category(cat_raw)
        except ValueError:
            category = profile_mod.guess_category(name)

        nutrition = nutrition_index.get(name, {})
        menu.append(
            MenuItem(
                item_id=str(row.get("id") or row.get("mealCode") or row.get("code") or name),
                name=name,
                category=category,
                price_cents=price_cents,
                tags=frozenset(profile_mod.extract_tags(name)),
                includes=tuple(row.get("includes") or ()),
                calories=nutrition.get("calories"),
                protein_g=nutrition.get("protein"),
            )
        )
    return menu


def _to_coupons(raw: list[dict[str, Any]]) -> list[Coupon]:
    coupons: list[Coupon] = []
    for row in raw:
        discount_raw = row.get("discount") or row.get("amount") or 0
        threshold_raw = row.get("threshold") or row.get("fullReduction") or 0
        try:
            discount = int(round(float(discount_raw) * 100)) if float(discount_raw) < 1000 else int(discount_raw)
            threshold = int(round(float(threshold_raw) * 100)) if float(threshold_raw) < 10000 else int(threshold_raw)
        except (TypeError, ValueError):
            continue
        if discount <= 0:
            continue
        coupons.append(
            Coupon(
                coupon_id=str(row.get("id") or row.get("couponId") or row.get("code") or discount),
                title=str(row.get("title") or row.get("name") or f"优惠¥{discount / 100:.2f}"),
                threshold_cents=threshold,
                discount_cents=discount,
                scope=str(row.get("scope") or "all"),
            )
        )
    return coupons


def _to_store(raw: dict[str, Any]) -> Store:
    distance = raw.get("distance") or raw.get("distanceMeters") or 0
    try:
        distance_m = int(distance)
    except (TypeError, ValueError):
        distance_m = 0
    fee_raw = raw.get("deliveryFee") or raw.get("deliveryFeeCents") or 0
    try:
        fee = int(round(float(fee_raw) * 100)) if float(fee_raw) < 1000 else int(fee_raw)
    except (TypeError, ValueError):
        fee = 0
    return Store(
        store_id=str(raw.get("id") or raw.get("storeId") or raw.get("code") or "store"),
        name=str(raw.get("name") or raw.get("storeName") or "麦当劳门店"),
        distance_meters=distance_m,
        walk_minutes=int(raw.get("walkMinutes") or max(1, round(distance_m / 80))),
        supports_pickup=bool(raw.get("supportsPickup", True)),
        supports_delivery=bool(raw.get("supportsDelivery", True)),
        delivery_fee_cents=fee,
        eta_minutes=int(raw.get("etaMinutes") or raw.get("deliveryTime") or 30),
    )


class OvertimeBite:
    """加班晚餐自动订餐助手。"""

    def __init__(self, provider: DataProvider, cfg: OvertimeConfig | None = None) -> None:
        self.provider = provider
        self.cfg = cfg or OvertimeConfig()

    def run(
        self,
        *,
        now: datetime | None = None,
        state: TriggerState | None = None,
        overtime_evidence: bool | None = None,
        store: Store | None = None,
        eating_at_desk: bool = True,
        dry_run: bool = True,
    ) -> Decision:
        """执行一次完整决策。

        Args:
            now: 当前时间；为 None 时从 MCP ``now-time-info`` 取，避免时区误判
            state: 幂等状态；为 None 时从本地文件读取
            overtime_evidence: 加班证据；None 表示未知（降级为需确认）
            store: 指定门店；为 None 时按地址自动选最近可配送门店
            eating_at_desk: 是否在工位用餐，影响渠道决策
            dry_run: **默认 True**。False 时会真实调用 ``create-order``
        """
        trace: list[str] = []

        # ---------- 1. 时间 ----------
        if now is None:
            now = self._now_from_mcp()
        trace.append(f"当前时间：{now:%Y-%m-%d %H:%M}")
        if state is None:
            state = TriggerState(date=f"{now:%Y-%m-%d}")

        # ---------- 2. 触发判定 ----------
        decision = trigger.evaluate(now, state, self.cfg, overtime_evidence=overtime_evidence)
        trace.append(f"触发判定：{decision.action.value} —— {decision.reason}")

        if decision.action is trigger.Action.SKIP:
            return Decision(action=decision.action, summary=decision.reason, verbose_trace=trace)

        if decision.action is trigger.Action.SILENT:
            return Decision(action=decision.action, summary="静默记一条建议，不打扰", verbose_trace=trace)

        # ---------- 3. 门店 ----------
        store = store or self._pick_store(trace)
        trace.append(f"选定门店：{store.name}（{store.distance_meters}m，配送费 ¥{store.delivery_fee_cents / 100:.2f}）")

        # ---------- 4. 口味画像 ----------
        history = self.provider.order_history(limit=20)
        learned = profile_mod.learn_from_orders(history, base=self.cfg.profile)
        profile_note = profile_mod.describe_profile(learned, len(history))
        trace.append("口味画像已更新")

        # ---------- 5. 领券（收益最高、风险最低的一步）----------
        try:
            self.provider.bind_all_coupons()
            trace.append("已一键领取当日可领优惠券")
        except Exception:  # noqa: BLE001 - 领券失败不应阻断主流程
            trace.append("一键领券失败，改用已有券继续")

        coupons = _to_coupons(self.provider.store_coupons(store.store_id))

        # ---------- 6. 菜单 ----------
        raw_menu = self.provider.meals(store.store_id)
        if not raw_menu:
            return Decision(
                action=decision.action,
                summary="该门店暂无可售餐品（可能已过供餐时段）",
                verbose_trace=trace,
            )
        menu = _to_menu(raw_menu)
        trace.append(f"菜单载入 {len(menu)} 个在售餐品，可用券 {len(coupons)} 张")

        # ---------- 7. 渠道决策（先渠道，后商品）----------
        ch = channel_mod.choose(
            store,
            budget_cents=self.cfg.budget_cap_cents,
            pickup_max_walk_meters=self.cfg.pickup_max_walk_meters,
            eating_at_desk=eating_at_desk,
        )
        trace.append(f"渠道决策：{ch.channel.value} —— {ch.reason}")

        # ---------- 8. 组合寻优 ----------
        candidates = optimizer.optimize(
            menu,
            coupons,
            budget_cents=self.cfg.budget_cap_cents,
            profile=learned,
            store=store,
            channel=ch.channel,
            max_items=self.cfg.max_items_per_order,
            max_coupons=self.cfg.max_coupons,
        )
        if not candidates:
            return Decision(
                action=decision.action,
                summary=f"预算 ¥{self.cfg.budget_cap_cents / 100:.0f} 内没有找到符合条件的组合"
                        f"（已排除忌口与辣度超限商品）",
                channel_decision=ch,
                verbose_trace=trace,
            )
        trace.append(f"寻优完成，{len(candidates)} 个候选方案，最高分 {candidates[0].score:.3f}")

        chosen = candidates[0]

        # ---------- 9. 价格以 MCP 为准 ----------
        try:
            verified = self.provider.calculate_price(
                {
                    "storeId": store.store_id,
                    "channel": ch.channel.value,
                    "items": [{"id": i.item_id, "quantity": 1} for i in chosen.items],
                    "couponIds": [c.coupon_id for c in chosen.coupons_used],
                }
            )
            trace.append("已用 calculate-price 向麦当劳侧复核金额")
            self._apply_verified_price(chosen, verified)
        except Exception:  # noqa: BLE001 - 复核失败不阻断，保留本地估算
            trace.append("价格复核失败，采用本地估算金额")

        # ---------- 10. 下单边界 ----------
        summary_lines = [chosen.describe(), *chosen.reasons]
        if decision.action is trigger.Action.CONFIRM or self.cfg.budget_cap_cents < 0:
            summary_lines.insert(0, "⚠️ 需要你确认后才会下单：")
            return Decision(
                action=trigger.Action.CONFIRM,
                summary="\n".join(summary_lines),
                candidates=candidates,
                chosen=chosen,
                channel_decision=ch,
                profile_note=profile_note,
                verbose_trace=trace,
            )

        if dry_run:
            summary_lines.insert(0, "🔍 演练模式，未真实下单：")
            return Decision(
                action=trigger.Action.ORDER,
                summary="\n".join(summary_lines),
                candidates=candidates,
                chosen=chosen,
                channel_decision=ch,
                profile_note=profile_note,
                verbose_trace=trace,
            )

        # ---------- 11. 真实下单 ----------
        try:
            result = self.provider.create_order(
                {
                    "storeId": store.store_id,
                    "channel": ch.channel.value,
                    "items": [{"id": i.item_id, "quantity": 1} for i in chosen.items],
                    "couponIds": [c.coupon_id for c in chosen.coupons_used],
                }
            )
            summary_lines.insert(0, "✅ 已下单：")
            return Decision(
                action=trigger.Action.ORDER,
                summary="\n".join(summary_lines),
                candidates=candidates,
                chosen=chosen,
                channel_decision=ch,
                profile_note=profile_note,
                order_result=result,
                verbose_trace=trace,
            )
        except Exception as exc:  # noqa: BLE001
            return Decision(
                action=trigger.Action.CONFIRM,
                summary=f"下单失败：{exc}\n方案已保留，可手动下单：{chosen.describe()}",
                candidates=candidates,
                chosen=chosen,
                channel_decision=ch,
                profile_note=profile_note,
                verbose_trace=trace,
            )

    # ---- 内部辅助 ----

    def _now_from_mcp(self) -> datetime:
        """用 MCP 的权威时间，避免 LLM 猜错时区或日期。"""
        try:
            data = self.provider.now()
        except Exception:  # noqa: BLE001 - 时间拿不到时退回本机时间
            return datetime.now()
        for key in ("datetime", "time", "currentTime", "dateTime"):
            value = data.get(key) if isinstance(data, dict) else None
            if not value:
                continue
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
                try:
                    return datetime.strptime(str(value), fmt)
                except ValueError:
                    continue
        return datetime.now()

    def _pick_store(self, trace: list[str]) -> Store:
        """按默认地址选最近的可配送门店。"""
        addresses = self.provider.my_addresses()
        address_text = None
        if addresses:
            first = addresses[0]
            address_text = " ".join(
                str(first.get(k, ""))
                for k in ("province", "city", "district", "detail", "address")
            ).strip() or None
        stores = self.provider.nearby_stores(address_text)
        if not stores:
            return Store(store_id="unknown", name="默认门店", distance_meters=1000, walk_minutes=12)
        return _to_store(min(stores, key=lambda s: int(s.get("distance") or s.get("distanceMeters") or 1e9)))

    @staticmethod
    def _apply_verified_price(candidate: Candidate, verified: Any) -> None:
        """用 MCP 返回的权威金额覆盖本地估算。

        刻意**不做**「由总价反推小计」——那样会在配送费或优惠口径不一致时
        把错误放大。这里只记录权威总价，商品明细保持原样以便对账。
        """
        if not isinstance(verified, dict):
            return
        total = verified.get("totalPrice") or verified.get("payable") or verified.get("total")
        if not isinstance(total, (int, float)):
            return
        candidate.verified_total_cents = int(total) if total >= 1000 else int(round(total * 100))