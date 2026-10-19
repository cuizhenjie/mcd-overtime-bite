"""编排管线：把触发、画像、渠道、寻优串成一次完整的加班晚餐决策。

执行顺序是有讲究的：**先判渠道，再选商品**。
因为 30 元预算下配送费是一个必须先剔除的常数项，
先把常数项算清楚，商品寻优的搜索空间才真实。
"""

from __future__ import annotations

import re
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


# 券标题形如「11.9元麦辣鸡腿堡」「9.9元中杯冰美式」，开头的数字是**券后实付价**。
# 这是麦当劳促销的命名惯例，且能用菜单原价交叉验证：券后价应当低于原价。
_COUPON_PRICE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*元")


def _coupon_price_cents(coupon: Coupon) -> int | None:
    """从券标题解析券后实付价（分）；解析不出返回 None。"""
    match = _COUPON_PRICE_RE.match(coupon.title.strip())
    if not match:
        return None
    return int(round(float(match.group(1)) * 100))


def _apply_coupon_prices(
    menu: list[MenuItem], coupons: list[Coupon]
) -> tuple[list[MenuItem], int]:
    """把券后价写进菜单，返回 (新菜单, 命中券的商品数)。

    为什么必须做：门店券把**定价藏在标题里**（``11.9元麦辣鸡腿堡`` 实为原价
    ¥22.5 的券后价）。若寻优器按原价算，会因为"太贵"而主动放弃这张券——
    实测中这直接导致最优方案没用券，浪费了 10 块钱。

    安全约束：只在 ``券后价 < 原价`` 时才替换，避免把非促销标题误读成降价。
    """
    if not coupons or not menu:
        return menu, 0

    overrides: dict[str, int] = {}
    for coupon in coupons:
        coupon_price = _coupon_price_cents(coupon)
        if coupon_price is None:
            continue
        for code in coupon.product_codes:
            item = next((m for m in menu if m.item_id == code), None)
            if item is None:
                continue
            if 0 < coupon_price < item.price_cents:
                overrides[code] = coupon_price

    if not overrides:
        return menu, 0

    updated: list[MenuItem] = []
    for item in menu:
        new_price = overrides.get(item.item_id)
        if new_price is None:
            updated.append(item)
        else:
            updated.append(
                MenuItem(
                    item_id=item.item_id,
                    name=item.name,
                    category=item.category,
                    price_cents=new_price,
                    tags=item.tags,
                    original_price_cents=item.price_cents,
                    includes=item.includes,
                    calories=item.calories,
                    protein_g=item.protein_g,
                )
            )
    return updated, len(overrides)


def _to_menu(
    raw: list[dict[str, Any]],
    nutrition_index: dict[str, dict] | None = None,
    coupons: list[Coupon] | None = None,
) -> list[MenuItem]:
    """把 ``McdDataProvider.menu_with_prices`` 的返回转成 MenuItem。

    真实菜单里存在**酱料**（韩式烟熏芝士风味酱 ¥3）与**周边玩具**
    （按摩捶痒痒挠 ¥14.9）——它们在菜单 API 里和正餐平级，若不剔除，
    寻优器完全可能把"一包酱"当成一顿饭推给用户。这里统一挡掉。
    """
    nutrition_index = nutrition_index or {}
    menu: list[MenuItem] = []
    for row in raw:
        name = str(row.get("name") or "").strip()
        if not name or profile_mod.is_non_food(name):
            continue

        price_cents = row.get("priceCents")
        if not price_cents:
            try:
                price_cents = int(round(float(row.get("price") or 0) * 100))
            except (TypeError, ValueError):
                price_cents = 0
        if price_cents <= 0:
            continue

        category = profile_mod.classify_item(name, row.get("categoryName"))
        nutrition = nutrition_index.get(name, {})
        tags = set(profile_mod.extract_tags(name))
        tags |= {str(t) for t in (row.get("tags") or [])}

        menu.append(
            MenuItem(
                item_id=str(row.get("code") or row.get("id") or name),
                name=name,
                category=category,
                price_cents=price_cents,
                tags=frozenset(tags),
                calories=nutrition.get("calories"),
                protein_g=nutrition.get("protein"),
            )
        )

    menu, _hit = _apply_coupon_prices(menu, list(coupons or []))
    return menu


def _to_coupons(raw: list[dict[str, Any]]) -> list[Coupon]:
    """把真实门店券转成 Coupon。

    真实结构是**商品型券**::

        {"title": "中薯条买一送一", "couponId": "...", "couponCode": "...",
         "products": [{"productCode": "9900012387", "productName": "中薯条买一送一"}],
         "promotionId": "..."}

    没有"满 X 减 Y"的金额字段，因此 ``discount_cents`` 留 0——
    宁可显示"无可计算优惠"，也不编造一个省 ¥X 的数字出来。
    """
    coupons: list[Coupon] = []
    for row in raw:
        products = row.get("products") or []
        product_codes = tuple(
            str(p.get("productCode")) for p in products
            if isinstance(p, dict) and p.get("productCode")
        )

        discount_raw = row.get("discount") or row.get("amount") or 0
        try:
            discount = int(round(float(discount_raw) * 100)) if 0 < float(discount_raw) < 1000 else int(discount_raw or 0)
        except (TypeError, ValueError):
            discount = 0

        threshold_raw = row.get("threshold") or row.get("fullReduction") or 0
        try:
            threshold = int(round(float(threshold_raw) * 100)) if 0 < float(threshold_raw) < 10000 else int(threshold_raw or 0)
        except (TypeError, ValueError):
            threshold = 0

        coupons.append(
            Coupon(
                coupon_id=str(row.get("couponId") or row.get("id") or row.get("code") or len(coupons)),
                title=str(row.get("title") or row.get("name") or "门店优惠"),
                threshold_cents=threshold,
                discount_cents=discount,
                scope="product" if product_codes else "all",
                product_codes=product_codes,
            )
        )
    return coupons


def _to_store(raw: dict[str, Any], be_type: int = 1) -> Store:
    """把门店查询结果转成 Store。

    真实字段是 ``storeCode``（纯数字）与可选的 ``beCode``：
    ``beCode`` 只在麦乐送/得来速场景存在，到店自取为 None，
    且官方明确要求「到店自提场景 beCode 传了会报错」。
    """
    distance_raw = raw.get("distance")
    try:
        distance_m = int(distance_raw or 0)
    except (TypeError, ValueError):
        distance_m = 0

    fee_raw = raw.get("deliveryFee") or raw.get("deliveryFeeCents") or 0
    try:
        fee = int(round(float(fee_raw) * 100)) if 0 < float(fee_raw) < 1000 else int(fee_raw or 0)
    except (TypeError, ValueError):
        fee = 0

    return Store(
        store_id=str(raw.get("storeCode") or raw.get("id") or raw.get("storeId") or "store"),
        name=str(raw.get("storeName") or raw.get("name") or "麦当劳门店"),
        distance_meters=distance_m,
        walk_minutes=int(raw.get("walkMinutes") or max(1, round(distance_m / 80))),
        supports_pickup=bool(raw.get("supportsPickup", be_type in (1, 5))),
        supports_delivery=bool(raw.get("supportsDelivery", be_type == 2)),
        delivery_fee_cents=fee,
        eta_minutes=int(raw.get("etaMinutes") or raw.get("deliveryTime") or 30),
        be_type=be_type,
        be_code=str(raw.get("beCode") or ""),
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
        history = self.provider.order_history()
        learned = profile_mod.learn_from_orders(history, base=self.cfg.profile)
        profile_note = profile_mod.describe_profile(learned, len(history))
        trace.append(f"口味画像已更新（基于 {len(history)} 笔真实历史订单）")

        # ---------- 5. 领券（收益最高、风险最低的一步）----------
        try:
            self.provider.bind_all_coupons()
            trace.append("已一键领取当日可领优惠券")
        except Exception:  # noqa: BLE001 - 领券失败不应阻断主流程
            trace.append("一键领券失败，改用已有券继续")

        coupons = _to_coupons(
            self.provider.store_coupons(
                store.store_id, be_code=store.be_code, be_type=store.be_type
            )
        )

        # ---------- 6. 菜单（带真实价格）----------
        raw_menu = self.provider.menu_with_prices(
            store.store_id, be_code=store.be_code, be_type=store.be_type
        )
        if not raw_menu:
            return Decision(
                action=decision.action,
                summary="该门店暂无可售餐品（可能已过供餐时段）",
                verbose_trace=trace,
            )
        menu = _to_menu(raw_menu, coupons=coupons)
        coupon_hits = sum(1 for m in menu if m.has_coupon_price)
        trace.append(
            f"菜单载入 {len(menu)} 个在售餐品（已剔除酱料/周边等非正餐条目），"
            f"可用券 {len(coupons)} 张，其中 {coupon_hits} 个商品命中券后价"
        )

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
        # 载荷字段名取自服务端 inputSchema：storeCode / beType / orderType / items[].productCode
        try:
            verified = self.provider.calculate_price(
                {
                    "storeCode": store.store_id,
                    "beType": store.be_type,
                    "orderType": 2 if ch.channel is Channel.DELIVERY else 1,
                    **({"beCode": store.be_code} if store.be_code else {}),
                    "items": [{"productCode": i.item_id, "quantity": 1} for i in chosen.items],
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
        """用 MCP 的权威时间，避免 LLM 猜错时区或日期。

        实测麦当劳 now-time-info 同时给出 ``data.datetime``（ISO，带 T）
        与 ``data.formatted``（空格分隔），两种格式都要认。
        """
        try:
            data = self.provider.now()
        except Exception:  # noqa: BLE001 - 时间拿不到时退回本机时间
            return datetime.now()
        if not isinstance(data, dict):
            return datetime.now()

        for key in ("formatted", "datetime", "time", "currentTime"):
            value = data.get(key)
            if not value:
                continue
            text = str(value).strip()
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
            ):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
        return datetime.now()

    def _pick_store(self, trace: list[str]) -> Store:
        """按配置的门店关键词选最近的一家。

        真实 ``query-nearby-stores`` 不收地址而是收 ``city`` + ``keyword``，
        且 ``beType`` 必传，因此这里优先走配置的城市/关键词；
        两者都没配时返回一家占位门店，让上层给出明确提示而不是静默失败。
        """
        city = (getattr(self.cfg, "city", "") or "").strip()
        keyword = (getattr(self.cfg, "store_keyword", "") or "").strip()
        if not city:
            return Store(
                store_id="", name="未配置门店", distance_meters=0,
                supports_delivery=False,
            )
        try:
            stores = self.provider.nearby_stores(city=city, keyword=keyword, be_type=1)
        except Exception as exc:  # noqa: BLE001
            trace.append(f"门店查询失败：{exc}")
            stores = []
        if not stores:
            return Store(store_id="", name=f"{city}（未找到门店）", distance_meters=0,
                         supports_delivery=False)
        best = min(stores, key=lambda s: int(s.get("distance") or 10**9))
        return _to_store(best, be_type=1)

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