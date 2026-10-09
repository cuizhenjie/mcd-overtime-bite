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
        """把 CallToolResult 解析成 dict，并对工具级错误做显式处理。

        **关键**：MCP 的工具执行失败时，协议层仍然返回 200，只是把
        ``isError`` 置为 true、内容放在 text 里。若不判断，下游会把这个
        错误字符串当成正常业务数据继续处理——非常隐蔽。
        """
        is_error = getattr(result, "isError", None)
        if is_error is None and isinstance(result, dict):
            is_error = result.get("isError")
        if is_error:
            raise McpError(f"{tool_name} 执行失败：{_extract_text(result) or '服务端未返回原因'}")

        text = _extract_text(result)
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}

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