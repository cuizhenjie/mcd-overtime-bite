"""真实麦当劳 MCP 返回结构的回归测试。

这里的每一条样本都来自**实际调用 mcp.mcd.cn** 抓到的真实响应，
不是按官方文档猜的格式。踩过的坑：

1. ``order-list`` 的商品字段是 ``orderProductList`` / ``productName``，
   不是文档里常见��� ``items`` / ``name``
2. 套餐内部另有 ``comboItemList``，内层字段名又变回 ``name``
3. 部分工具返回纯 JSON，部分返回 **markdown 字段说明 + "## Original Response" + JSON**
4. 业务失败时 HTTP 仍是 200、``isError`` 仍可能是 false，
   真正的错误信号是响应外壳里的 ``success: false``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from overtime_bite.mcp_client import (  # noqa: E402
    McpError,
    parse_mcp_payload,
    unwrap_envelope,
)

# ---- 真实响应样本（脱敏后保留结构）----

REAL_NOW_TIME_TEXT = """# API Response Information

Below is the response from an API call. To help you understand the data, I've provided:

1. A detailed description of all fields in the response structure
2. The complete API response

## Response Structure

> Content-Type: application/json

- **data**: 服务器时间信息 (Type: object)
  - **data.datetime**: ISO格式日期时间 (Type: string)

## Original Response

{"success":true,"code":200,"message":"请求成功","datetime":"2026-10-09 17:54:03","traceId":"c1d4","data":{"timestamp":1791539643574,"datetime":"2026-10-09T17:54:03.574","formatted":"2026-10-09 17:54:03","date":"2026-10-09","dayOfWeek":"FRIDAY","timezone":"GMT+08:00"}}
"""

REAL_ORDER = {
    "orderId": "1030545170000751729306611439",
    "orderType": "1",
    "createTime": "2026-06-05 20:39:19",
    "storeCode": "1950823",
    "storeName": "麦当劳北京西马场路得来速餐厅",
    "orderStatus": "订单已完成",
    "orderProductList": [
        {
            "productCode": "9900011798",
            "productName": "爆脆星星堡三件套",
            "quantity": 1,
            "comboItemList": [
                {"productCode": "521289", "name": "爆脆星星堡", "quantity": 1},
                {"productCode": "4810", "name": "中薯条", "quantity": 1},
                {"productCode": "515281", "name": "阳光柠檬红茶中杯", "quantity": 1},
            ],
        }
    ],
}

REAL_SIMPLE_ORDER = {
    "createTime": "2026-06-04 19:42:35",
    "orderProductList": [
        {"productCode": "X1", "productName": "麦辣鸡腿汉堡", "quantity": 1}
    ],
}


# ---- markdown 包裹解析 ----


def test_parse_markdown_wrapped_response():
    """形态 2：markdown 说明 + Original Response 后的 JSON。"""
    payload = parse_mcp_payload(REAL_NOW_TIME_TEXT)
    assert payload is not None, "markdown 包裹的响应必须能解析出来"
    assert payload["success"] is True
    assert payload["data"]["timezone"] == "GMT+08:00"


def test_parse_plain_json_response():
    """形态 1：纯 JSON。同一 Server 里混用两种形态。"""
    assert parse_mcp_payload("[]") == []
    assert parse_mcp_payload('{"a": 1}') == {"a": 1}


def test_parse_returns_none_on_garbage():
    """解析不出来必须是 None，不能返回半截字符串冒充数据。"""
    assert parse_mcp_payload("这不是 JSON") is None
    assert parse_mcp_payload("") is None


def test_parse_code_fenced_json():
    text = '## Original Response\n\n```json\n{"success": true, "data": {"x": 1}}\n```'
    payload = parse_mcp_payload(text)
    assert payload == {"success": True, "data": {"x": 1}}


# ---- 响应外壳 ----


def test_unwrap_returns_data():
    payload = parse_mcp_payload(REAL_NOW_TIME_TEXT)
    data = unwrap_envelope(payload)
    assert data["formatted"] == "2026-10-09 17:54:03"


def test_unwrap_raises_on_success_false():
    """业务失败时 HTTP 200 + isError false，只有 success 字段能识别。"""
    payload = {"success": False, "code": 40001, "message": "门店已打烊"}
    try:
        unwrap_envelope(payload)
    except McpError as exc:
        assert "门店已打烊" in str(exc)
        return
    raise AssertionError("success=False 必须抛出 McpError")


def test_unwrap_passthrough_without_envelope():
    assert unwrap_envelope({"plain": 1}) == {"plain": 1}
    assert unwrap_envelope([1, 2]) == [1, 2]


# ---- 真实订单结构 ----


def test_flatten_expands_combo_items():
    """套餐必须展开成内层单品，否则画像学的是"套餐"这个外壳。"""
    from overtime_bite.profile import flatten_order_items

    items = flatten_order_items(REAL_ORDER)
    names = [i["name"] for i in items]
    assert names == ["爆脆星星堡", "中薯条", "阳光柠檬红茶中杯"], names
    assert "爆脆星星堡三件套" not in names, "套餐外壳不应计入"


def test_flatten_handles_plain_product():
    from overtime_bite.profile import flatten_order_items

    items = flatten_order_items(REAL_SIMPLE_ORDER)
    assert [i["name"] for i in items] == ["麦辣鸡腿汉堡"]


def test_learn_from_real_order_structure():
    """用真实结构喂画像学习，必须真的学到东西（之前因字段不匹配恒为空）。"""
    from overtime_bite.profile import learn_from_orders

    orders = [REAL_ORDER, REAL_SIMPLE_ORDER, REAL_SIMPLE_ORDER, REAL_ORDER]
    profile = learn_from_orders(orders)
    assert profile.likes, "真实字段下应能学到品类偏好"
    assert profile.likes["burger"] > 0
    assert profile.spicy_tolerance >= 1


def test_now_time_parsing_handles_iso_and_formatted():
    """真实时间返回同时有 ISO 和空格分隔两种格式，两种都要认。"""
    from overtime_bite import OvertimeBite, OvertimeConfig
    from overtime_bite.mock import MockProvider

    for text, expected in (("2026-10-09T17:54:03.574", (2026, 10, 9, 17, 54)),
                           ("2026-10-09 17:54:03", (2026, 10, 9, 17, 54))):
        provider = MockProvider()
        provider.now = lambda t=text: {"datetime": t}  # type: ignore[method-assign]
        got = OvertimeBite(provider, OvertimeConfig())._now_from_mcp()  # noqa: SLF001 - 白盒验证
        assert (got.year, got.month, got.day, got.hour, got.minute) == expected, (text, got)


def test_full_mcp_unwrap_chain_on_real_sample():
    """客户端主链路：markdown → envelope → dict。"""
    from overtime_bite.mcp_client import McdMcpClient

    class _Result:
        isError = False
        content = [type("B", (), {"text": REAL_NOW_TIME_TEXT})()]

    out = McdMcpClient._unwrap(_Result(), "now-time-info")
    assert out["timezone"] == "GMT+08:00", out


def _run() -> int:
    passed, failed = 0, []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
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