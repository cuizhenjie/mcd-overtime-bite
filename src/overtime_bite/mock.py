"""模拟数据源：让项目在**没有 Token、没有网络**的情况下也能完整跑通和演示。

这不是"假数据凑数"，而是一个契约替身：
``MockProvider`` 与 ``McdDataProvider`` 实现同一套协议，
所以演示路径和真实路径走的是同一份 pipeline 代码。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# 菜单价格用「元」，模拟 MCP 的真实返回格式
_SAMPLE_MENU: list[dict[str, Any]] = [
    {"id": "C01", "name": "巨无霸套餐", "category": "combo", "price": 39.0},
    {"id": "C02", "name": "培根蔬萃双层牛堡套餐", "category": "combo", "price": 42.0},
    {"id": "C03", "name": "麦辣鸡腿堡套餐", "category": "combo", "price": 33.0},
    {"id": "B01", "name": "吉士汉堡", "category": "burger", "price": 18.0},
    {"id": "B02", "name": "巨无霸", "category": "burger", "price": 22.0},
    {"id": "B03", "name": "麦辣鸡腿汉堡", "category": "burger", "price": 17.0},
    {"id": "B04", "name": "香辣鸡腿堡", "category": "burger", "price": 17.0},
    {"id": "S01", "name": "中薯条", "category": "side", "price": 7.0},
    {"id": "S02", "name": "麦乐鸡（5块）", "category": "side", "price": 16.0},
    {"id": "S03", "name": "香辣鸡翅（2块）", "category": "side", "price": 9.0},
    {"id": "D01", "name": "中可乐", "category": "drink", "price": 7.0},
    {"id": "D02", "name": "中无糖可乐", "category": "drink", "price": 7.0},
    {"id": "D03", "name": "燕麦拿铁", "category": "drink", "price": 15.0},
    {"id": "T01", "name": "蛋挞（2只）", "category": "dessert", "price": 8.0},
    {"id": "T02", "name": "麦旋风", "category": "dessert", "price": 10.0},
]

_SAMPLE_STORE = {
    "id": "S00123",
    "name": "麦当劳（望京SOHO店）",
    "distance": 420,
    "walkMinutes": 5,
    "supportsPickup": True,
    "supportsDelivery": True,
    "deliveryFee": 6.0,
    "etaMinutes": 32,
}

_SAMPLE_COUPONS = [
    {"id": "CP01", "title": "满30减6", "threshold": 30.0, "discount": 6.0},
    {"id": "CP02", "title": "满20减3", "threshold": 20.0, "discount": 3.0},
    {"id": "CP03", "title": "汉堡单单减4", "threshold": 0.0, "discount": 4.0},
]

_SAMPLE_HISTORY: list[dict[str, Any]] = [
    {"orderNo": "1001", "items": [
        {"name": "吉士汉堡", "category": "burger"},
        {"name": "中薯条", "category": "side"},
        {"name": "中可乐", "category": "drink"},
    ]},
    {"orderNo": "1002", "items": [
        {"name": "巨无霸", "category": "burger"},
        {"name": "中薯条", "category": "side"},
        {"name": "中无糖可乐", "category": "drink"},
    ]},
    {"orderNo": "1003", "items": [
        {"name": "吉士汉堡", "category": "burger"},
        {"name": "中可乐", "category": "drink"},
    ]},
    {"orderNo": "1004", "items": [
        {"name": "麦辣鸡腿堡套餐", "category": "combo"},
        {"name": "蛋挞（2只）", "category": "dessert"},
    ]},
]


class MockProvider:
    """离线替身。默认不产生任何真实副作用。"""

    def __init__(
        self,
        menu: list[dict[str, Any]] | None = None,
        store: dict[str, Any] | None = None,
        coupons: list[dict[str, Any]] | None = None,
        history: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
        *,
        create_order_raises: bool = False,
    ) -> None:
        self._menu = menu if menu is not None else _SAMPLE_MENU
        self._store = store if store is not None else _SAMPLE_STORE
        self._coupons = coupons if coupons is not None else _SAMPLE_COUPONS
        self._history = history if history is not None else _SAMPLE_HISTORY
        self._now = now
        self._create_order_raises = create_order_raises
        self.calls: list[str] = []
        self.bound_coupons = False

    def now(self) -> dict[str, Any]:
        self.calls.append("now-time-info")
        base = self._now or datetime(2026, 10, 9, 19, 30)
        return {"datetime": base.strftime("%Y-%m-%d %H:%M:%S")}

    def my_addresses(self) -> list[dict[str, Any]]:
        self.calls.append("delivery-query-addresses")
        return [{"id": "A1", "city": "北京市", "district": "朝阳区", "detail": "望京SOHO T1"}]

    def nearby_stores(self, address: str | None = None) -> list[dict[str, Any]]:
        self.calls.append("query-nearby-stores")
        return [self._store]

    def meals(self, store_id: str) -> list[dict[str, Any]]:
        self.calls.append("query-meals")
        return list(self._menu)

    def store_coupons(self, store_id: str) -> list[dict[str, Any]]:
        self.calls.append("query-store-coupons")
        return list(self._coupons)

    def bind_all_coupons(self) -> dict[str, Any]:
        self.calls.append("auto-bind-coupons")
        self.bound_coupons = True
        return {"success": True, "bound": len(self._coupons)}

    def order_history(self, limit: int = 20) -> list[dict[str, Any]]:
        self.calls.append("order-list")
        return list(self._history[:limit])

    def calculate_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        """模拟 ``calculate-price``：按真实口径算小计、配送费、优惠、应付。

        刻意把配送费和优惠都算进来——mock 少算一项，
        演示输出就会和真实麦当劳对不上，反而误导使用者。
        """
        self.calls.append("calculate-price")
        price_of = {m["id"]: float(m["price"]) for m in self._menu}
        subtotal = sum(
            price_of.get(item.get("id"), 0.0) * int(item.get("quantity", 1))
            for item in payload.get("items", [])
        )
        fee = float(self._store.get("deliveryFee", 0)) if payload.get("channel") == "delivery" else 0.0

        coupons = {c["id"]: c for c in self._coupons}
        discount = 0.0
        for cid in payload.get("couponIds", []):
            c = coupons.get(cid)
            if not c:
                continue
            threshold = float(c.get("threshold", 0))
            if subtotal >= threshold:
                discount += min(float(c.get("discount", 0)), subtotal)

        payable = max(0.0, subtotal + fee - discount)
        return {
            "subtotal": round(subtotal, 2),
            "deliveryFee": round(fee, 2),
            "discount": round(discount, 2),
            "payable": round(payable, 2),
            "totalPrice": round(payable, 2),
        }

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("create-order")
        if self._create_order_raises:
            raise RuntimeError("模拟下单失败：门店已打烊")
        return {"orderNo": "MCD20261009999", "payUrl": "https://example.invalid/pay", "status": "created"}


def default_provider(now: datetime | None = None) -> MockProvider:
    return MockProvider(now=now)