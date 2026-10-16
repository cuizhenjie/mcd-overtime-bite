"""模拟数据源：让项目在**没有 Token、没有网络**的情况下也能完整跑通和演示。

这不是"假数据凑数"，而是一个契约替身：
``MockProvider`` 与 ``McdDataProvider`` 实现同一套协议，
所以演示路径和真实路径走的是同一份 pipeline 代码。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .mcp_client import McpError

# 菜单价格用「元」，模拟 MCP 的真实返回格式
_SAMPLE_MENU: list[dict[str, Any]] = [
    {"id": "C01", "name": "龙焰芝士棒鸡腿堡三件套", "price": 29.9, "categoryName": "汉堡", "tags": ["套餐"]},
    {"id": "C02", "name": "培根安格斯厚牛堡三件套", "price": 45.5, "categoryName": "汉堡", "tags": ["套餐"]},
    {"id": "C03", "name": "麦辣鸡腿堡三件套", "price": 26.0, "categoryName": "汉堡", "tags": ["套餐"]},
    {"id": "B01", "name": "吉士汉堡", "price": 18.0, "categoryName": "汉堡"},
    {"id": "B02", "name": "巨无霸", "price": 22.0, "categoryName": "汉堡"},
    {"id": "B03", "name": "麦辣鸡腿汉堡", "price": 17.0, "categoryName": "汉堡"},
    {"id": "B04", "name": "香辣鸡腿堡", "price": 17.0, "categoryName": "汉堡"},
    {"id": "S01", "name": "中薯条", "price": 14.5, "categoryName": "小食甜品/其他"},
    {"id": "S02", "name": "麦乐鸡5块", "price": 15.0, "categoryName": "炸鸡"},
    {"id": "S03", "name": "麦辣鸡翅2块", "price": 15.0, "categoryName": "炸鸡"},
    {"id": "D01", "name": "可乐", "price": 9.5, "categoryName": "饮品"},
    {"id": "D02", "name": "阳光柠檬红茶小杯", "price": 9.5, "categoryName": "饮品"},
    {"id": "D03", "name": "冰燕麦奶铁中杯", "price": 23.0, "categoryName": "麦咖啡"},
    {"id": "T01", "name": "圆筒冰淇淋", "price": 5.0, "categoryName": "小食甜品/其他"},
    {"id": "T02", "name": "经典麦旋风", "price": 14.5, "categoryName": "小食甜品/其他"},
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

# 真实门店券是**商品型**「买一送一」，绑定 productCode，没有可计算的减免金额。
# 这里刻意用真实结构而非自造的满减券——自造会让演示凭空显示"省 ¥10"。
_SAMPLE_COUPONS = [
    {"couponId": "8692944B045928451618BFF22F535D2A", "title": "中薯条买一送一",
     "products": [{"productCode": "S01", "productName": "中薯条买一送一"}]},
    {"couponId": "E05F6BFB4BAFAA5DB44B233583BFE711", "title": "王牌小食拼",
     "products": [{"productCode": "S02", "productName": "王牌小食拼"}]},
]

_SAMPLE_HISTORY: list[dict[str, Any]] = [
    # 结构取自真实 mcp.mcd.cn 的 order-list 响应（已脱敏）：
    # 商品字段是 orderProductList / productName，套餐内层是 comboItemList / name。
    # 用真实结构而非自造结构——之前自造的 items/name 根本匹配不上真实响应，
    # 导致离线测试全绿、画像学习在真实数据上却恒为空。
    {"orderId": "1001", "createTime": "2026-06-05 20:39:19",
     "storeName": "麦当劳北京西马场路得来速餐厅",
     "orderProductList": [
         {"productCode": "9900011798", "productName": "爆脆星星堡三件套", "quantity": 1,
          "comboItemList": [
              {"productCode": "521289", "name": "爆脆星星堡", "quantity": 1},
              {"productCode": "4810", "name": "中薯条", "quantity": 1},
              {"productCode": "515281", "name": "阳光柠檬红茶中杯", "quantity": 1},
          ]}]},
    {"orderId": "1002", "createTime": "2026-06-04 19:42:35",
     "storeName": "麦当劳北京西马场路得来速餐厅",
     "orderProductList": [
         {"productCode": "X1", "productName": "麦辣鸡腿汉堡", "quantity": 1}]},
    {"orderId": "1003", "createTime": "2026-06-04 19:33:14",
     "storeName": "麦当劳北京西马场路得来速餐厅",
     "orderProductList": [
         {"productCode": "521289", "productName": "爆脆星星堡", "quantity": 1}]},
    {"orderId": "1004", "createTime": "2026-06-04 19:31:37",
     "storeName": "麦当劳北京西马场路得来速餐厅",
     "orderProductList": [
         {"productCode": "Y2", "productName": "麦麦脆汁鸡-带骨里脊", "quantity": 1},
          {"productCode": "Y3", "productName": "迷你薯条", "quantity": 1},
          {"productCode": "Y4", "productName": "无糖可口可乐中杯", "quantity": 1}]},
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

    def nearby_stores(self, *, city: str = "", keyword: str = "", be_type: int = 1,
                      search_type: int = 2) -> list[dict[str, Any]]:
        """签名与真实 ``query-nearby-stores`` 一致。"""
        self.calls.append("query-nearby-stores")
        if not city:
            raise McpError("麦当劳接口返回失败：城市名或者关键词不能为空")
        return [self._store]

    def menu_with_prices(self, store_code: str, *, be_type: int = 1, order_type: int = 1,
                         be_code: str = "") -> list[dict[str, Any]]:
        """与真实 provider 同构：直接产出带名称与价格的菜单条目。"""
        self.calls.append("query-meals")
        self.calls.append("calculate-price")
        out = []
        for m in self._menu:
            out.append({
                "code": m["id"],
                "name": m["name"],
                "price": m["price"],
                "priceCents": int(round(float(m["price"]) * 100)),
                "categoryName": m.get("categoryName", ""),
                "tags": m.get("tags", []),
            })
        return out

    def meals(self, store_code: str, *, be_code: str = "", be_type: int = 1,
              order_type: int = 1) -> list[dict[str, Any]]:
        self.calls.append("query-meals")
        return [
            {"name": m.get("categoryName", "菜单"), "meals": [{"code": m["id"], "tags": []}]}
            for m in self._menu
        ]

    def store_coupons(self, store_code: str, *, be_code: str = "", be_type: int = 1,
                      order_type: int = 1) -> list[dict[str, Any]]:
        self.calls.append("query-store-coupons")
        return list(self._coupons)

    def bind_all_coupons(self) -> dict[str, Any]:
        self.calls.append("auto-bind-coupons")
        self.bound_coupons = True
        return {"success": True, "bound": len(self._coupons)}

    def order_history(self) -> list[dict[str, Any]]:
        self.calls.append("order-list")
        return list(self._history)

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