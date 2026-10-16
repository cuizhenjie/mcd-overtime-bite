"""麦当劳 MCP Server 接入层。

**为什么要有这一层**：核心决策逻辑（触发/寻优/渠道）应该能在没有 Token、
没有网络的环境下被完整测试。MCP 的网络与鉴权细节全部收敛在这个文件里。

本模块不硬依赖 ``mcp`` 包：没装或没 Token 时抛出带修复指引的异常，
而不是让上层拿到一个难以理解的堆栈。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

MCP_URL = "https://mcp.mcd.cn"
TOKEN_ENV = "MCD_MCP_TOKEN"

# calculate-price 单次可带的商品数上限。实测 117 件会报「商品超过数量限制」，
# 30 件稳定通过，故按此分批。
PRICE_CHUNK_SIZE = 30

# 麦当劳 MCP 工具名（与官方 mcd-mcp-server v1.0.9 文档一致）
TOOL_NOW_TIME = "now-time-info"
TOOL_QUERY_ADDRESSES = "delivery-query-addresses"
TOOL_QUERY_STORES = "delivery-query-stores"
TOOL_NEARBY_STORES = "query-nearby-stores"
TOOL_QUERY_MEALS = "query-meals"
TOOL_MEAL_DETAIL = "query-meal-detail"
TOOL_NUTRITION = "list-nutrition-foods"
TOOL_CALC_PRICE = "calculate-price"
TOOL_CREATE_ORDER = "create-order"
TOOL_CANCEL_ORDER = "cancel-order"
TOOL_QUERY_ORDER = "query-order"
TOOL_ORDER_LIST = "order-list"
TOOL_STORE_COUPONS = "query-store-coupons"
TOOL_AVAILABLE_COUPONS = "available-coupons"
TOOL_AUTO_BIND_COUPONS = "auto-bind-coupons"
TOOL_MY_COUPONS = "query-my-coupons"


class McpError(RuntimeError):
    """MCP 调用失败。"""


class McpNotConfigured(McpError):
    """未配置 Token 或缺少依赖。"""


@dataclass
class McdMcpClient:
    """麦当劳 MCP 客户端。

    用法::

        client = McdMcpClient.from_env()
        now = client.call(TOOL_NOW_TIME)
    """

    token: str
    url: str = MCP_URL
    timeout_seconds: float = 60.0
    # 自定义 httpx 客户端工厂。留空则用 mcp 内置实现。
    #
    # 为什么需要这个口子：在设置了 ALL_PROXY=socks5://... 的环境里，
    # httpx 会尝试走 SOCKS 代理；若未安装 socksio，连接会直接报
    # "Using SOCKS proxy, but the 'socksio' package is not installed"。
    # 这在国内开发机上非常常见。两种解法二选一：
    #   1) pip install "httpx[socks]"  —— 希望 MCP 走代理
    #   2) 传入 trust_env=False 的工厂 —— 走直连（本地测试常用）
    httpx_client_factory: Any = None

    def __post_init__(self) -> None:
        # 这些是运行期状态，不是 dataclass 字段。在 __post_init__ 里初始化，
        # 避免被当成字段参与 __init__ 与 __repr__。
        self._loop = None
        self._thread = None
        self._session = None
        self._stack = None
        self._hold = None
        self._connect_error: Exception | None = None

    @classmethod
    def from_env(cls) -> "McdMcpClient":
        token = os.environ.get(TOKEN_ENV, "").strip()
        if not token:
            raise McpNotConfigured(
                f"未找到 MCP Token。请访问 {MCP_URL} 用手机号登录后申请，"
                f"然后执行：export {TOKEN_ENV}=<你的token>"
            )
        return cls(token=token)

    # ---- 连接管理 ----

    def _ensure_session(self) -> Any:
        """惰性建立 Streamable HTTP 会话，并保证它在多次调用之间**存活**。

        为什么需要这么绕：MCP 的 ``ClientSession`` 是 anyio task group 支撑的异步对象，
        一旦它所在的 ``async with`` 块退出，``__aexit__`` 会 cancel 掉整个 task group，
        session 随即失效。而 ``OvertimeBite`` 的对外接口是同步的（定时任务场景）。

        所以这里采用「常驻协程 + 跨线程派发」的结构：
          1. 起一个私有事件循环线程，跑 ``run_forever``
          2. 在其上启动一个**永不返回**的 ``_connect_and_hold``，用 AsyncExitStack 持有连接
          3. 每次工具调用用 ``run_coroutine_threadsafe`` 投递回同一循环

        踩过的坑：早期实现里 ``_connect`` 用完 ``async with`` 就返回，
        session 在第一次调用之前就已经被关闭了——接真实 MCP 必炸。
        """
        if self._session is not None:
            return self._session

        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:  # pragma: no cover - 取决于运行环境
            raise McpNotConfigured("缺少 mcp 依赖。请执行：pip install mcp") from exc

        import asyncio
        import threading

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="mcd-mcp"
        )
        self._thread.start()

        ready = threading.Event()
        try:
            self._hold = asyncio.run_coroutine_threadsafe(
                self._connect_and_hold(streamablehttp_client, ClientSession, ready),
                self._loop,
            )
        except Exception as exc:  # noqa: BLE001
            self._shutdown_loop()
            raise McpError(f"MCP 会话启动失败：{exc}") from exc

        if not ready.wait(timeout=30):
            self.close()
            raise McpError("MCP 会话建立超时（30s）。请检查网络与 Token 是否有效")

        if self._connect_error is not None:
            error, self._connect_error = self._connect_error, None
            self.close()
            raise McpError(f"MCP 连接失败：{error}") from error

        if self._session is None:
            self.close()
            raise McpError("MCP 会话建立失败：session 为空")
        return self._session

    async def _connect_and_hold(self, http_client, session_cls, ready: Any) -> None:
        """在事件循环上建立连接并**持续持有**，直到 close() 被调用。

        这个协程正常情况下永不返回——它持有的 AsyncExitStack 一旦退出，
        session 就会被销毁。
        """
        import asyncio
        from contextlib import AsyncExitStack

        headers = {"Authorization": f"Bearer {self.token}"}
        factory = self.httpx_client_factory
        try:
            stack = AsyncExitStack()
            await stack.__aenter__()
            try:
                # streamablehttp_client 自己负责 httpx 客户端的创建与关闭，
                # 这里只把工厂透传下去即可。
                extra = {"httpx_client_factory": factory} if factory is not None else {}
                streams = await stack.enter_async_context(
                    http_client(self.url, headers=headers, **extra)
                )
                read, write, _ = streams
                session = await stack.enter_async_context(session_cls(read, write))
                await session.initialize()
                self._session = session
                self._stack = stack
            except BaseException:
                await stack.aclose()
                raise
        except BaseException as exc:  # noqa: BLE001
            self._connect_error = exc
            ready.set()
            return

        ready.set()
        # 阻塞直到 close() 取消本协程，连接生命周期由此维持
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            raise
        finally:
            self._session = None

    def _shutdown_loop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        self._loop = None
        self._thread = None

    # ---- 调用 ----

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用一个 MCP 工具并返回解析后的结果。

        Raises:
            McpError: 工具返回错误或调用超时
        """
        self._ensure_session()
        import asyncio

        future = asyncio.run_coroutine_threadsafe(
            self._call_async(tool_name, arguments or {}), self._loop
        )
        try:
            result = future.result(timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise McpError(f"调用 {tool_name} 超时（{self.timeout_seconds}s）") from exc
        except Exception as exc:  # noqa: BLE001 - 统一包装
            raise McpError(f"调用 {tool_name} 失败：{exc}") from exc

        return self._unwrap(result, tool_name)

    async def _call_async(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """必须在持有 session 的那个事件循环上执行。"""
        if self._session is None:
            raise McpError("MCP 会话不可用")
        return await self._session.call_tool(tool_name, arguments)

    @staticmethod
    def _unwrap(result: Any, tool_name: str) -> dict[str, Any]:
        """把 CallToolResult 解析成 dict，并处理两层错误。

        三种失败形态都要拦住，否则会以"正常数据"的形式流到业务逻辑里：
          1. 协议级错误 —— ``CallToolResult.isError``
          2. 业务级错误 —— 响应外壳里 ``success: false``（HTTP 仍是 200）
          3. markdown 包裹 / 纯 JSON 两种返回形态
        """
        is_error = getattr(result, "isError", None)
        if is_error is None and isinstance(result, dict):
            is_error = result.get("isError")
        if is_error:
            raise McpError(f"{tool_name} 执行失败：{_extract_text(result) or '服务端未返回原因'}")

        text = _extract_text(result)
        payload = parse_mcp_payload(text)
        if payload is None:
            if not text.strip():
                return {}
            # 解析不出来就原样暴露，不能悄悄变成空 dict 掩盖问题
            return {"raw": text}

        payload = unwrap_envelope(payload)
        if isinstance(payload, dict):
            return payload
        return {"items": payload} if isinstance(payload, list) else {"raw": payload}

    def close(self) -> None:
        """关闭会话并停止事件循环线程。可重复调用。"""
        import asyncio

        loop, self._loop = self._loop, None
        hold, self._hold = self._hold, None
        if loop is None:
            return

        if hold is not None:
            try:
                hold.cancel()
                stack = getattr(self, "_stack", None)
                if stack is not None:
                    asyncio.run_coroutine_threadsafe(stack.aclose(), loop)
            except Exception:  # noqa: BLE001 - 关闭失败无需向上抛出
                pass
            self._stack = None

        self._session = None
        self._shutdown_loop()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "McdMcpClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _extract_text(result: Any) -> str:
    """从 MCP CallToolResult 中取出文本内容。"""
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "\n".join(parts)


# 麦当劳 MCP 实测至少有三种返回形态，同一个 Server 里混用：
#   1) 纯 JSON，例如 delivery-query-addresses → []
#   2) markdown 字段说明 + "## Original Response" + JSON（now-time-info）
#   3) 一段描述文字 + JSON + 又一段描述文字（query-nearby-stores）
# 形态 3 连固定锚点都没有，只能靠扫描定位 JSON 的起止，因此统一用
# raw_decode 逐个候选位置试探。
_ORIGINAL_RESPONSE = "## Original Response"


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()


def parse_mcp_payload(text: str) -> Any:
    """把 MCP 返回的文本解析成 Python 对象，三种形态都支持。

    策略：先试整体解析，再试 ``## Original Response`` 锚点，
    最后用 ``raw_decode`` 扫描所有 ``{`` / ``[`` 起点，找出第一个能完整
    解出 JSON 的位置——形态 3 的 JSON 前后都夹着散文，只有扫描才找得到。

    解析不出来返回 None，由调用方降级；绝不返回半截字符串冒充数据。
    """
    if not text:
        return None
    candidate = text.strip()

    # 形态 1：整体就是 JSON
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 形态 2：固定锚点
    idx = candidate.rfind(_ORIGINAL_RESPONSE)
    if idx >= 0:
        tail = _strip_code_fence(candidate[idx + len(_ORIGINAL_RESPONSE):])
        try:
            return json.loads(tail)
        except json.JSONDecodeError:
            pass

    # 形态 3：在散文中扫描 JSON 起点
    #
    # ⚠️ 不能"第一个能解出来的位置"就算数——描述文字里到处都是
    # ``data[].storeName`` 这种写法，raw_decode 会在那个 ``[`` 处
    # 成功解出一个**空数组**，把后面真正的响应信封整个跳过去。
    # 所以按优先级扫三遍：带 success 的信封对象 > 任意对象 > 任意值。
    decoder = json.JSONDecoder()
    positions = [i for i, ch in enumerate(candidate) if ch in "{["]
    fallbacks: list[Any] = []

    for i in positions:
        try:
            value, _ = decoder.raw_decode(candidate, i)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if "success" in value:
                return value
            fallbacks.append(value)
        elif not fallbacks:
            fallbacks.append(value)

    if fallbacks:
        return fallbacks[0]
    return None


def unwrap_envelope(payload: Any) -> Any:
    """剥离麦当劳的统一响应外壳，取出 ``data``。

    真实外壳形如::

        {"success": true, "code": 200, "message": "请求成功",
         "traceId": "...", "data": {... 或 [...]}}

    关键点：**业务失败时 HTTP 仍是 200、isError 仍可能是 false**，
    只看 isError 会把 ``{"success": false, "message": "..."}`` 当成正常数据。

    Returns:
        data 字段的内容；若无外壳则原样返回。

    Raises:
        McpError: success 为 false 时
    """
    if isinstance(payload, dict) and "success" in payload:
        if not payload.get("success", True):
            message = payload.get("message") or f"code={payload.get('code')}"
            raise McpError(f"麦当劳接口返回失败：{message}")
        if "data" in payload:
            return payload["data"]
    return payload


# ---- 语义化封装：让 pipeline 不需要关心工具名和字段名 ----


class McdDataProvider:
    """把麦当劳 MCP 的原始返回，翻译成 OvertimeBite 的领域模型。

    这一层的存在还有一个好处：**换 MCP 版本时只需要改这里**。

    ⚠️ 参数名全部来自 ``list_tools()`` 实际返回的 inputSchema，不是猜的。
    麦当劳的工具有几个反直觉之处，改动前务必核对：
      - ``query-nearby-stores`` 收 ``city`` + ``keyword`` + **必传** ``beType``，
        没有 ``address`` 参数
      - ``query-meals`` / ``query-store-coupons`` 收 ``storeCode`` + ``beCode``
        + ``beType`` + ``orderType``，不是 ``storeId``
      - ``order-list`` **不接受任何参数**
    """

    # beType 取值（服务端枚举）
    BE_PICKUP = 1        # 到店自提
    BE_DELIVERY = 2     # 麦乐送
    BE_DRIVE_THRU = 5   # 得来速车道取餐
    BE_CATERING = 6     # 企业团餐

    def __init__(self, client: McdMcpClient) -> None:
        self._client = client

    def now(self) -> dict[str, Any]:
        return self._client.call(TOOL_NOW_TIME)

    def my_addresses(self) -> list[dict[str, Any]]:
        """配送地址列表（外送场景）。"""
        data = self._client.call(TOOL_QUERY_ADDRESSES)
        return _as_list(data, ("addresses", "list", "data"))

    def nearby_stores(
        self,
        *,
        city: str,
        keyword: str = "",
        be_type: int = BE_PICKUP,
        search_type: int = 2,
    ) -> list[dict[str, Any]]:
        """按城市/关键词检索门店。

        Args:
            city: 城市名，必填（缺失会返回 code=600058）
            keyword: 地址/商圈关键词，如「望京」
            be_type: 1 到店自提 / 2 麦乐送 / 5 得来速
            search_type: 2 按位置搜索（默认）；1 只搜收藏餐厅
        """
        args: dict[str, Any] = {"beType": be_type, "searchType": search_type}
        if city:
            args["city"] = city
        if keyword:
            args["keyword"] = keyword
        data = self._client.call(TOOL_NEARBY_STORES, args)
        return _as_list(data, ("stores", "list", "data"))

    def delivery_stores(self, address_id: str) -> list[dict[str, Any]]:
        """按已保存地址查可配送门店（外送场景）。"""
        data = self._client.call(TOOL_DELIVERY_STORES, {
            "addressId": address_id,
            "beType": self.BE_DELIVERY,
        })
        return _as_list(data, ("stores", "list", "data"))

    def meals(
        self,
        store_code: str,
        *,
        be_code: str = "",
        be_type: int = BE_PICKUP,
        order_type: int = 1,
    ) -> list[dict[str, Any]]:
        """门店在售菜单。

        Args:
            store_code: ``storeCode``，纯数字，来自门店查询结果
            be_code: ``beCode``，与 storeCode 配对；得来速/外送场景必传
            be_type: 业务类型
            order_type: 1 到店 / 2 外送
        """
        args: dict[str, Any] = {"storeCode": store_code, "beType": be_type, "orderType": order_type}
        if be_code:
            args["beCode"] = be_code
        data = self._client.call(TOOL_QUERY_MEALS, args)
        return _as_list(data, ("meals", "menu", "list", "data"))

    def store_coupons(
        self,
        store_code: str,
        *,
        be_code: str = "",
        be_type: int = BE_PICKUP,
        order_type: int = 1,
    ) -> list[dict[str, Any]]:
        """该门店当前可用券。参数与 :meth:`meals` 同构。"""
        args: dict[str, Any] = {"storeCode": store_code, "beType": be_type, "orderType": order_type}
        if be_code:
            args["beCode"] = be_code
        data = self._client.call(TOOL_STORE_COUPONS, args)
        return _as_list(data, ("coupons", "list", "data"))

    def bind_all_coupons(self) -> dict[str, Any]:
        """一键领取所有可领券——这是省钱动作里收益最高、风险最低的一步。

        注意：这是**写操作**，会真实把券领进用户账户，调用前应取得用户确认。
        """
        return self._client.call(TOOL_AUTO_BIND_COUPONS)

    def available_coupons(self) -> list[dict[str, Any]]:
        """可领取的券（只读）。"""
        data = self._client.call(TOOL_AVAILABLE_COUPONS)
        return _as_list(data, ("coupons", "list", "data"))

    def my_coupons(self) -> list[dict[str, Any]]:
        """账户已有券。"""
        data = self._client.call(TOOL_MY_COUPONS)
        return _as_list(data, ("coupons", "list", "data"))

    def order_history(self) -> list[dict[str, Any]]:
        """历史订单。实测该工具**不接受任何参数**。"""
        data = self._client.call(TOOL_ORDER_LIST)
        return _as_list(data, ("orders", "list", "data"))

    def nutrition(self) -> dict[str, Any]:
        return self._client.call(TOOL_NUTRITION)

    def calculate_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        """交给 MCP 做权威价格计算。

        **重要**：本地寻优器只用来"生成候选"，最终金额一律以本工具返回为准，
        避免自己算错优惠导致下单金额与展示不符。

        金额单位是**分**（subtotal=2990 表示 ¥29.90）。
        """
        return self._client.call(TOOL_CALC_PRICE, payload)

    def menu_with_prices(
        self,
        store_code: str,
        *,
        be_type: int = BE_PICKUP,
        order_type: int = 1,
        be_code: str = "",
    ) -> list[dict[str, Any]]:
        """取回「带真实名称与价格」的完整菜单。

        为什么需要这个方法：`query-meals` 只返回分类 + 商品 ``code`` 与 ``tags``，
        **既没有名称也没有价格**；``query-meal-detail`` 能拿到名称但仍无价格。
        真正的价格在 ``calculate-price`` 的 ``productList`` 里，而且它同时
        返回了 ``productName`` —— 于是「菜单」可以被 2 次调用完整还原：

            query-meals      → 分类结构 + 全部商品 code
            calculate-price  → 每个 code 的名称与单价

        实测约束（来自真实调用）：
          - 单次商品数超过约 30~50 会报「商品超过数量限制」，故按 30 分批
          - **请求条数多于返回条数**：部分商品会被服务端静默剔除
            （不可点/需选配的组合等）。因此只采信返回结果，
            缺席即视为"当前不可点"，正好是寻优器该知道的事
        """
        cats = self.meals(store_code, be_code=be_code, be_type=be_type, order_type=order_type)

        entries: list[tuple[str, str, list[str]]] = []
        for cat in cats:
            cat_name = str(cat.get("name") or "")
            for item in cat.get("meals") or []:
                code = item.get("code")
                if not code:
                    continue
                entries.append((str(code), cat_name, list(item.get("tags") or [])))
        if not entries:
            return []

        priced: dict[str, dict[str, Any]] = {}
        for start in range(0, len(entries), PRICE_CHUNK_SIZE):
            chunk = entries[start:start + PRICE_CHUNK_SIZE]
            try:
                result = self.calculate_price({
                    "storeCode": store_code,
                    "beType": be_type,
                    "orderType": order_type,
                    **({"beCode": be_code} if be_code else {}),
                    "items": [{"productCode": code, "quantity": 1} for code, _, _ in chunk],
                })
            except McpError:
                # 单批失败不应让整份菜单作废，跳过即可
                continue
            for row in (result or {}).get("productList") or []:
                code = row.get("productCode")
                if not code:
                    continue
                priced[str(code)] = {
                    "code": str(code),
                    "name": row.get("productName") or "",
                    "price_cents": int(row.get("subtotal") or 0),
                    "discount_cents": int(row.get("discount") or 0),
                }

        result_menu: list[dict[str, Any]] = []
        for code, cat_name, tags in entries:
            info = priced.get(code)
            if not info or info["price_cents"] <= 0:
                continue  # 服务端没返价格 → 当前不可点，不进搜索空间
            result_menu.append({
                "code": code,
                "name": info["name"],
                "price": info["price_cents"] / 100,
                "priceCents": info["price_cents"],
                "categoryName": cat_name,
                "tags": tags,
            })
        return result_menu

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """**真实下单，不可逆**。调用前必须取得用户明确确认。"""
        return self._client.call(TOOL_CREATE_ORDER, payload)


def _as_list(data: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """MCP 返回结构不完全一致，这里做一次宽松提取。

    实际遇到的形态：可能是 list，也可能已由 ``_unwrap`` 包成 ``{"items": [...]}``。
    """
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        # _unwrap 对 list 结果统一包成了 {"items": [...]}
        items = data.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


Provider = Callable[..., Any]