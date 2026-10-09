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
    _session: Any = None

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
        """惰性建立 Streamable HTTP 会话。

        采用同步 ClientSession 跑在一个私有事件循环线程里，
        这样可以同时被同步代码（如定时任务）和异步代码调用。
        """
        if self._session is not None:
            return self._session

        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:  # pragma: no cover - 取决于运行环境
            raise McpNotConfigured(
                "缺少 mcp 依赖。请执行：pip install mcp"
            ) from exc

        import asyncio
        import threading

        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _runner() -> None:
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._connect(loop, streamablehttp_client, ClientSession))
            finally:
                ready.set()

        thread = threading.Thread(target=_runner, daemon=True, name="mcd-mcp")
        thread.start()
        ready.wait(timeout=30)
        if self._session is None:
            raise McpError("MCP 会话建立超时")
        return self._session

    async def _connect(self, loop, http_client, ClientSession) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with http_client(self.url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                # 会话需在事件循环存活期间有效
                loop.call_later(3600, loop.stop)

    # ---- 调用 ----

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用一个 MCP 工具并返回解析后的结果。

        Raises:
            McpError: 工具返回错误时
        """
        session = self._ensure_session()
        try:
            result = session.call_tool(tool_name, arguments or {})
        except Exception as exc:  # noqa: BLE001 - 统一包装上层难以理解的异常
            raise McpError(f"调用 {tool_name} 失败：{exc}") from exc

        text = _extract_text(result)
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {"raw": text}

    def close(self) -> None:
        self._session = None


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


# ---- 语义化封装：让 pipeline 不需要关心工具名和字段名 ----


class McdDataProvider:
    """把麦当劳 MCP 的原始返回，翻译成 OvertimeBite 的领域模型。

    这一层的存在还有一个好处：**换 MCP 版本时只需要改这里**。
    """

    def __init__(self, client: McdMcpClient) -> None:
        self._client = client

    def now(self) -> dict[str, Any]:
        return self._client.call(TOOL_NOW_TIME)

    def my_addresses(self) -> list[dict[str, Any]]:
        data = self._client.call(TOOL_QUERY_ADDRESSES)
        return _as_list(data, ("addresses", "list", "data"))

    def nearby_stores(self, address: str | None = None) -> list[dict[str, Any]]:
        args = {"address": address} if address else {}
        data = self._client.call(TOOL_NEARBY_STORES, args)
        return _as_list(data, ("stores", "list", "data"))

    def meals(self, store_id: str) -> list[dict[str, Any]]:
        data = self._client.call(TOOL_QUERY_MEALS, {"storeId": store_id})
        return _as_list(data, ("meals", "menu", "list", "data"))

    def store_coupons(self, store_id: str) -> list[dict[str, Any]]:
        data = self._client.call(TOOL_STORE_COUPONS, {"storeId": store_id})
        return _as_list(data, ("coupons", "list", "data"))

    def bind_all_coupons(self) -> dict[str, Any]:
        """一键领取所有可领券——这是省钱动作里收益最高、风险最低的一步。"""
        return self._client.call(TOOL_AUTO_BIND_COUPONS)

    def my_coupons(self) -> list[dict[str, Any]]:
        data = self._client.call(TOOL_MY_COUPONS)
        return _as_list(data, ("coupons", "list", "data"))

    def order_history(self, limit: int = 20) -> list[dict[str, Any]]:
        data = self._client.call(TOOL_ORDER_LIST, {"limit": limit})
        return _as_list(data, ("orders", "list", "data"))

    def nutrition(self) -> dict[str, Any]:
        return self._client.call(TOOL_NUTRITION)

    def calculate_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        """交给 MCP 做权威价格计算。

        **重要**：本地寻优器只用来"生成候选"，最终金额一律以本工具返回为准，
        避免自己算错优惠导致下单金额与展示不符。
        """
        return self._client.call(TOOL_CALC_PRICE, payload)

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.call(TOOL_CREATE_ORDER, payload)


def _as_list(data: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """MCP 返回结构不完全一致，这里做一次宽松提取。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


Provider = Callable[..., Any]