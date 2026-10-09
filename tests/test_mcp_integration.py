"""真实 MCP 协议集成测试。

与其它测试不同，这里会**真的起一个 MCP 服务器**（streamable-http），
用真实的 `ClientSession` 走完整的 initialize → call 流程。

它守的是一个已经踩过的坑：``ClientSession`` 由 anyio task group 支撑，
一旦它所在的 ``async with`` 块退出就会被取消。如果连接协程用完就返回，
**第一次调用时 session 早已死亡**——而这个 bug 在纯 mock 测试里完全看不出来。

所以这里刻意做多次连续调用，并断言 Authorization 头真的送达了服务端。
"""

from __future__ import annotations

import socket
import sys
import threading
import time

import uvicorn
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from mcp.server.fastmcp import FastMCP

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    MCP_AVAILABLE = False

from overtime_bite.mcp_client import McdMcpClient, McpError  # noqa: E402

_SERVER = None
_PORT = None
_RECEIVED_HEADERS: list[dict] = []
_LOCK = threading.Lock()


def _direct_factory(headers=None, timeout=None, **kwargs):
    """绕过本机 SOCKS 代理直连。

    开发机上普遍设置了 ALL_PROXY=socks5://...，httpx 会尝试走代理；
    未安装 socksio 时会直接抛错。localhost 根本没��要代理。
    """
    import httpx

    return httpx.AsyncClient(headers=headers or {}, timeout=timeout, trust_env=False)


def _client(url: str, timeout: float = 20.0) -> "McdMcpClient":
    return McdMcpClient(
        token="test-token", url=url, timeout_seconds=timeout, httpx_client_factory=_direct_factory
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_server() -> "FastMCP":
    """一个极简的「麦当劳 MCP」替身：真实协议、真实鉴权校验、真实工具名。

    工具名刻意使用麦当劳官方文档里的**连字符**写法（now-time-info），
    而不是 FastMCP 默认从函数名推导的下划线形式——
    否则测的就不是同一个协议面了。
    """
    server = FastMCP("mcd-test", host="127.0.0.1", port=_PORT)

    @server.tool(name="now-time-info")
    def now_time_info() -> dict:
        """获取当前时间信息"""
        return {"datetime": "2026-10-09 19:30:00"}

    @server.tool(name="query-meals")
    def query_meals(storeId: str) -> dict:  # noqa: N803 - 与 MCP 工具签名保持一致
        """查询当前可售卖的餐品列表"""
        return {
            "meals": [
                {"id": "B02", "name": "巨无霸", "category": "burger", "price": 22.0},
                {"id": "S01", "name": "中薯条", "category": "side", "price": 7.0},
                {"id": "D01", "name": "中可乐", "category": "drink", "price": 7.0},
            ]
        }

    @server.tool(name="order-list")
    def order_list(limit: int = 20) -> dict:
        """查询历史订单"""
        return {
            "orders": [
                {"orderNo": "1001", "items": [{"name": "巨无霸", "category": "burger"}]},
                {"orderNo": "1002", "items": [{"name": "巨无霸", "category": "burger"}]},
                {"orderNo": "1003", "items": [{"name": "中薯条", "category": "side"}]},
            ][:limit]
        }

    @server.tool(name="create-order")
    def create_order(storeId: str) -> dict:  # noqa: N803
        """创建订单——测试绝不应触发它"""
        raise RuntimeError("create-order 不应被调用")

    return server


class _HeaderCapture:
    """纯 ASGI 中间件：记录每个 HTTP 请求真实收到的头。

    用 ASGI 层而不是框架装饰器，是为了不依赖 Starlette/FastMCP 的构建顺序。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http":
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            with _LOCK:
                _RECEIVED_HEADERS.append(headers)
        await self.app(scope, receive, send)


def start_server() -> int:
    global _SERVER, _PORT
    _PORT = _free_port()
    _SERVER = _build_server()

    # 直接跑 ASGI app，并在外面包一层中间件记录真实收到的请求头。
    # 这样"Authorization 有没有真的发出去"就不是靠推断，而是实测。
    app = _HeaderCapture(_SERVER.streamable_http_app())

    thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=_PORT, log_level="warning"),
        daemon=True,
        name="mcp-test-server",
    )
    thread.start()

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", _PORT), timeout=0.5):
                return _PORT
        except OSError:
            time.sleep(0.15)
    raise RuntimeError("测试 MCP 服务器启动超时")


def _last_authorization() -> str | None:
    with _LOCK:
        for headers in reversed(_RECEIVED_HEADERS):
            if "authorization" in headers:
                return headers["authorization"]
        return None


# ---- 测试 ----


def test_session_survives_multiple_calls():
    """回归测试：连接协程若提前退出，第二次调用必然失败。"""
    port = start_server()
    url = f"http://127.0.0.1:{port}/mcp"
    with _client(url) as client:
        first = client.call("now-time-info")
        assert first.get("datetime") == "2026-10-09 19:30:00", first

        second = client.call("query-meals", {"storeId": "S00123"})
        meals = second.get("meals")
        assert meals and len(meals) == 3, second

        third = client.call("order-list", {"limit": 10})
        assert len(third.get("orders", [])) == 3, third


def test_arguments_are_forwarded():
    port = start_server()
    url = f"http://127.0.0.1:{port}/mcp"
    with _client(url) as client:
        # limit=1 应只返回 1 单，证明参数确实传到了服务端
        result = client.call("order-list", {"limit": 1})
        assert len(result.get("orders", [])) == 1, result


def test_auth_header_is_sent():
    """实测：Authorization 头必须真的出现在服务端的请求里。

    麦当劳服务端对缺失/错误 Token 直接返回 401，而 FastMCP 替身不校验，
    所以这里用中间件把收到的请求头抓下来断言，而不是"调用没报错就算过"。
    """
    port = start_server()
    url = f"http://127.0.0.1:{port}/mcp"
    token = "Bearer-secret-abc123"

    with McdMcpClient(
        token=token, url=url, timeout_seconds=20, httpx_client_factory=_direct_factory
    ) as client:
        client.call("now-time-info")

    assert _last_authorization() == f"Bearer {token}", (
        f"Authorization 未按 'Bearer <token>' 送达服务端，实际收到：{_last_authorization()}"
    )


def test_from_env_reads_token():
    from overtime_bite.mcp_client import TOKEN_ENV
    import os

    os.environ[TOKEN_ENV] = "Bearer-secret-abc123"
    try:
        assert McdMcpClient.from_env().token == "Bearer-secret-abc123"
    finally:
        del os.environ[TOKEN_ENV]


def test_from_env_without_token_raises():
    from overtime_bite.mcp_client import TOKEN_ENV, McpNotConfigured
    import os

    saved = os.environ.pop(TOKEN_ENV, None)
    try:
        try:
            McdMcpClient.from_env()
        except McpNotConfigured:
            return
        raise AssertionError("缺少 Token 时应抛出 McpNotConfigured")
    finally:
        if saved is not None:
            os.environ[TOKEN_ENV] = saved


def test_unknown_tool_raises_mcp_error():
    port = start_server()
    url = f"http://127.0.0.1:{port}/mcp"
    with _client(url) as client:
        try:
            client.call("no_such_tool_xyz")
        except McpError:
            return
        raise AssertionError("调用不存在的工具应当抛出 McpError")


def test_connection_to_dead_port_fails_fast():
    dead = _free_port()
    client = _client(f"http://127.0.0.1:{dead}/mcp", timeout=5)
    try:
        client.call("now-time-info")
    except McpError:
        return
    finally:
        client.close()
    raise AssertionError("连接到空端口应当抛出 McpError")


def test_close_is_idempotent():
    port = start_server()
    url = f"http://127.0.0.1:{port}/mcp"
    client = _client(url)
    client.call("now-time-info")
    client.close()
    client.close()  # 再关一次不应抛异常


def test_pipeline_against_real_mcp_server():
    """完整管线跑在真实 MCP 协议之上（用 McdDataProvider 适配）。"""
    from overtime_bite import OvertimeBite, OvertimeConfig, TriggerState
    from overtime_bite.mcp_client import McdDataProvider
    from datetime import datetime

    port = start_server()
    url = f"http://127.0.0.1:{port}/mcp"

    class PartialProvider(McdDataProvider):
        """本地测试服务器只实现了部分工具，其余走安全的空实现。"""

        def nearby_stores(self, address=None):
            return [{"id": "S00123", "name": "麦当劳（测试店）", "distance": 420,
                     "walkMinutes": 5, "deliveryFee": 6.0, "etaMinutes": 32}]

        def my_addresses(self):
            return [{"id": "A1", "city": "北京市", "detail": "望京"}]

        def auto_bind_coupons(self):
            return {"success": True}

        def store_coupons(self, store_id):
            return [{"id": "CP01", "title": "满30减6", "threshold": 30.0, "discount": 6.0}]

        def calculate_price(self, payload):
            return {"subtotal": 29.0, "deliveryFee": 6.0, "discount": 6.0, "payable": 29.0}

    with _client(url) as client:
        provider = PartialProvider(client)
        bot = OvertimeBite(provider, OvertimeConfig(budget_cap_cents=3000))
        decision = bot.run(
            now=datetime(2026, 10, 9, 19, 30),
            state=TriggerState(date="2026-10-09"),
            overtime_evidence=True,
            dry_run=True,
        )

    assert decision.chosen is not None, decision.summary
    assert decision.chosen.within_budget(3000), decision.chosen.describe()
    assert "巨无霸" in decision.chosen.describe() or "薯条" in decision.chosen.describe()


def _run() -> int:
    if not MCP_AVAILABLE:
        print("mcp 未安装，跳过集成测试（pip install 'mcp>=1.0,<2.0'）")
        return 0

    passed, failed = 0, []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")

    print(f"\n{passed} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())