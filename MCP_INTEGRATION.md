# MCP 集成说明

本文说明 OvertimeBite 实际使用的麦当劳 MCP Server、调用到的 Tool、完整调用流程，以及这些能力带来的业务价值。

- **MCP Server**：`https://mcp.mcd.cn`（Streamable HTTP，麦当劳中国远程托管）
- **协议版本参考**：`mcd-mcp-server` v1.0.9
- **鉴权**：`Authorization: Bearer <MCP_TOKEN>`，Token 从 `MCD_MCP_TOKEN` 环境变量读取，仓库内不含任何真实凭证

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

---

## 一、Tool 使用清单

OvertimeBite 实际调用 11 个 Tool，覆盖 5 个业务场景。参数名均取自服务端
`list_tools()` 返回的 inputSchema（实测值，详见第六节）。

| # | Tool 名称 | 在本项目中的用途 | 对应模块 |
|---|-----------|------------------|---------|
| 1 | `now-time-info` | 获取权威当前时间，避免 LLM 猜测时区/日期导致的触发误判 | `pipeline._now_from_mcp` |
| 2 | `delivery-query-addresses` | 读取用户已保存的配送地址，作为门店检索的地理锚点 | `pipeline._pick_store` |
| 3 | `query-nearby-stores` | 查询地址附近麦当劳门店，返回距离、步行时间、配送费、ETA | `pipeline._pick_store` |
| 4 | `order-list` | 拉取历史订单（该工具不接受参数），从中学习品类偏好与口味标签 | `profile.learn_from_orders` |
| 5 | `auto-bind-coupons` | 一键领取当日全部可领券——收益最高、风险最低的省钱动作 | `pipeline`（寻优前置） |
| 6 | `query-store-coupons` | 查询当前门店可用券，作为叠券计算输入 | `pipeline` |
| 7 | `query-meals` + `calculate-price` | 菜单只有分类与商品 code，**名称与价格要从 `calculate-price` 取**，两次调用拼出完整菜单 | `mcp_client.menu_with_prices` |
| 8 | `calculate-price` | **权威金额复核**：小计、优惠、应付总额 | `pipeline._apply_verified_price` |
| 9 | `create-order` | 创建订单并返回支付链接 | `pipeline`（仅 `dry_run=False`） |
| 10 | `cancel-order` | 供误触发后的撤销兜底（接口已封装于 `mcp_client`） | 预留 |
| 11 | `query-order` | 订单状态追踪 | 预留 |

> 说明：本项目另实现了 `list-nutrition-foods` 的接入（`McdDataProvider.nutrition`），
> 用于按热量修正饱腹度估算；因部分门店菜单未返回热量字段，该能力为**可选增强**。

---

## 二、完整调用流程

一次加班晚餐决策的 MCP 调用序列：

```
 ① now-time-info                ──▶  权威时间
        │
        ▼  触发判定（时间窗口 / 加班证据 / 幂等 / 每日上限）
        │  ┈┈ 不通过 → 结束，不产生任何写操作
        │
 ② query-nearby-stores          ──▶  附近门店（city + keyword + beType）
        │
        ▼  选定最近可配送门店
        │
 ④ order-list                   ──▶  历史订单（orderProductList / comboItemList）
        │                           └─▶ 学习口味画像（贝叶斯收缩）
        ▼
 ⑤ auto-bind-coupons            ──▶  一键领券（先领后算，否则用不上当天券）
 ⑥ query-store-coupons          ──▶  该店可用券
 ⑦ query-meals                  ──▶  分类结构 + 商品 code（无名称无价格）
 ⑧ calculate-price × N          ──▶  商品名称与单价（按 30 件分批）
        │
        ▼  渠道决策（先渠道，后商品）
        │  ┈┈ 配送费 ≥ 预算 20% 且门店在步行范围内 → 改走到店自取
        ▼
    组合寻优（预算硬约束 + 忌口/辣度过滤 + 多目标评分）
        │
 ⑨ calculate-price              ──▶  最终方案金额复核
        │
        ▼  触发判定为「需确认」 →  停止，返回方案供人工确认
        ▼  触发判定为「可下单」且 dry_run=False
 ⑩ create-order                 ──▶  订单 + 支付链接
        │
       （误触发时）
 ⑩ cancel-order / query-order   ──▶  撤销 / 追踪
```

### 为什么是这个顺序

**先领券再选商品**：券会改变可行解空间。若先按原价选品再算优惠，可能因为超预算而被迫丢弃本来用券后完全可行的方案（例：33 元套餐 + 6 元配送 = 39 元超预算，满 30 减 6 后为 33 元，仍超；但叠加满 20 减 3 + 汉堡减 4 后为 26 元，完全可行）。先扩空间再寻优，才能挖出这类方案。

**先渠道后商品**：配送费是常数项。先定渠道，商品寻优才在真实的成本空间里进行，而不是"选完商品才发现配送费吃掉了预算"。

**最后才复核金额**：本地寻优器只负责**生成候选**，不负责裁决价格。最终应付金额一律以 `calculate-price` 返回为准，并写入 `Candidate.verified_total_cents` 覆盖本地估算。这样展示金额与实际扣款永远一致。

---

## 三、业务价值

### 1. 把「点餐」从注意力密集任务降级为后台任务

加班时最贵的不是钱，是**被打断的成本**。一次点餐决策要占用 10 分钟注意力，而那 10 分钟本应用于休息。本项目把这条链路压到零主动交互——时间到了，饭自己安排好。

### 2. 触达门店侧夜间闲置产能

加班场景集中在 20:00–23:00，这恰好是麦当劳非高峰时段。自动化的晚餐需求能填补晚餐后与夜宵前的空档订单，提升门店闲时坪效，同时让加班人群获得稳定、负担得着的正餐——供需两侧同时受益。

### 3. 让券的沉睡率下降

用户往往不知道当天有券可领、领了也不会在点餐时想起来用。`auto-bind-coupons` + `query-store-coupons` + 叠券寻优的组合，把"券"从一种营销资产变成了**实际被消耗的资产**，同时降低用户的实际支出。

在 30 元预算下，叠券的效果尤为显著：实测示例中 29 元商品 + 6 元配送 = 35 元，叠加满 30 减 6、满 20 减 3、汉堡减 4 后为 **28 元**，正好卡在预算内。若不叠券，该组合会被直接判为超预算而放弃。

### 4. 渠道选择带来的隐性节省

`query-nearby-stores` 同时返回距离、步行时间与配送费，使得「到店自取 vs 麦乐送外送」成为一个可计算的最优化问题，而非用户的主观偏好。在 30 元预算、6 元配送费的场景下，推荐自取相当于直接节省 20% 的可支配预算。

### 5. 口味画像形成可积累的用户资产

`order-list` 提供的历史订单被转化为结构化偏好，让推荐质量随使用时长提升。相比"每次重新描述一遍我吃什么"，这是一个**自我强化**的产品体验。

---

## 四、错误处理与限流

| 情况 | 处理方式 |
|------|---------|
| MCP 401（Token 无效/过期） | `McpError` 明确提示重新申请 Token，不静默重试 |
| MCP 429（超过 600 次/分钟） | 官方限流策略为每分钟 600 次；本项目单次决策仅 ~9 次调用，远低于阈值 |
| 领券失败 | 降级为使用已有券继续，不阻断主流程 |
| 门店无在售餐品（过供餐时段） | 返回明确说明，不构造虚假方案 |
| 价格复核失败 | 保留本地估算并标注，不阻断 |
| 下单失败 | 降级为"需确认"，同时保留已选方案供手动下单 |
| 时间获取失败 | 回退本机时间，并继续执行（触发引擎对时间有容错窗口） |

---

## 五、复现方式

仓库提供**离线可运行**的完整链路：

```bash
python examples/run_demo.py
```

演示使用 `MockProvider`，它与 `McdDataProvider` 实现同一套 `DataProvider` 协议，因此演示路径与真实路径走的是**同一份 pipeline 代码**。切换到真实 MCP 只需：

```python
# src/overtime_bite/mock.py
provider = MockProvider()

# 换成真实连接
provider = McdDataProvider(McdMcpClient.from_env())
```

---

## 六、接入层的实测约束

以下是拿真实 Token 连上 `mcp.mcd.cn` 后逐条核对的结果，**全部来自服务端 `list_tools()`
返回的 inputSchema 与实际调用响应**，不是按文档推断的。

### 1. 工具参数名极易猜错

| 工具 | 实际参数 | 常见误解 |
|---|---|---|
| `query-nearby-stores` | `city` + `keyword` + **`beType`（必传）** + `searchType` | 没有 `address` 参数 |
| `query-meals` | `storeCode` + `beCode` + `beType` + `orderType` | 不是 `storeId` |
| `query-store-coupons` | 同上 | 不是 `storeId` |
| `order-list` | **不接受任何参数** | 传 `limit` 无意义 |
| `calculate-price` | `storeCode` + `beType` + `orderType` + `items[].productCode` | 不是 `items[].id` |

`city` 与 `keyword` 至少要有一个，否则返回 `code=600058 城市名或者关键词不能为空`。

`beCode` 只在麦乐送/得来速场景存在；官方明确说明**到店自提场景传了会报错**。

### 2. 菜单没有价格——价格只存在于 `calculate-price`

这是最反直觉的一点，也是整个寻优器能否成立的前提：

- `query-meals` 只返回 **分类结构 + 商品 `code` + `tags`**，既无名称也无价格
- `query-meal-detail` 能拿到 `name`、套餐轮次、图片，**仍然没有价格**
- 真正的价格在 `calculate-price` 的 `productList` 里（`subtotal` 单位是**分**，
  `subtotal=2990` 即 ¥29.90），而且它**同时返回 `productName`**

于是菜单可以被 2 次调用完整还原：

```
query-meals      -> 分类结构 + 全部商品 code
calculate-price  -> 每个 code 的名称与单价
```

`McdDataProvider.menu_with_prices()` 封装了这个过程，117 项菜单实测约 2.8 秒。

**两个实测约束：**

- 单次商品数过多（117 件）会报「商品超过数量限制」，故按 **30 件分批**
- **请求条数多于返回条数**（请求 30 只回 27）——部分商品被服务端静默剔除。
  因此只采信返回结果，缺席即视为"当前不可点"，不进搜索空间

### 3. 返回形态有三种，且混用

| 形态 | 例子 |
|---|---|
| 纯 JSON | `delivery-query-addresses` -> `[]` |
| markdown + `## Original Response` + JSON | `now-time-info` |
| 散文 + JSON + 尾部散文 | `query-nearby-stores` |

第三种没有固定锚点，只能扫描定位。注意**不能"第一个能解析的位置"就算数**——
描述文字里到处都是 `data[].storeName`，`raw_decode` 会在那个 `[` 处成功解出
**空数组**，把后面真正的响应整个跳过。正确策略是按优先级扫三遍：
带 `success` 的信封对象 > 任意对象 > 任意值。

### 4. 业务错误的信号是 `success`，不是 `isError`

业务失败时 HTTP 仍是 200、`CallToolResult.isError` 仍可能是 `false`：

```json
{"success": false, "code": 600058, "message": "城市名或者关键词不能为空"}
```

不判断的话，这段错误内容会被当成正常业务数据继续向下流动。

### 5. 菜单里混着非正餐条目

真实菜单中确实存在「韩式烟熏芝士风味酱 ¥3.00」与「按摩捶痒痒挠 ¥14.90」，
它们在菜单 API 里与正餐**平级**。若不剔除，寻优器完全可能把"一包酱"当成一顿饭。

`_to_menu()` 统一挡掉；品类判定则优先用 API 返回的权威分类名（`categoryName`），
而不是只靠商品名猜测。

### 6. 所需配置

| 配置项 | 说明 |
|---|---|
| `city` | 城市名，必填，留空则门店查询失败 |
| `store_keyword` | 商圈/路口关键词，如「西马场路」 |
| `MCD_MCP_TOKEN` | 环境变量 |

---

## 七、其他工程约束

### `mcp` 必须锁在 1.x

mcp 2.x 是破坏性升级，对本项目有致命影响：

| 变化 | 影响 |
|---|---|
| `FastMCP` -> `MCPServer` | 服务端侧改名 |
| `streamablehttp_client` -> `streamable_http_client` | 客户端 API 改名 |
| **`StreamableHTTPTransport` 不再接受 `headers`** | **麦当劳强制要求 `Authorization`，缺头直接 401** |

所以 `requirements.txt` 锁定 `mcp>=1.0,<2.0`，与麦当劳官方文档保持一致。

### session 生命周期必须显式持有

`ClientSession` 由 anyio task group 支撑，其 `__aexit__` 会 cancel 整个 task group。
最初的连接实现用完 `async with` 就返回，**导致 session 在第一次调用前就已失效**——
而纯 mock 测试完全无法发现这个问题。

现在的做法是让连接协程用 `AsyncExitStack` **常驻持有**连接直到 `close()`，
每次调用通过 `run_coroutine_threadsafe` 派发回同一事件循环。

### 代理环境下的连接方式

开发机普遍设置 `ALL_PROXY=socks5://...`，httpx 会尝试走 SOCKS 代理；
未安装 `socksio` 时连接直接失败。两种解法：

```bash
pip install "httpx[socks]"     # 希望 MCP 走代理
```

或在代码中传入 `trust_env=False` 的工厂直连（国内服务走代理没必要）：

```python
import httpx
def factory(headers=None, timeout=None, **kw):
    return httpx.AsyncClient(headers=headers or {}, timeout=timeout, trust_env=False)

client = McdMcpClient.from_env()
client.httpx_client_factory = factory
```

---

## 八、测试

| 测试文件 | 数量 | 依赖 |
|---|---:|---|
| `test_trigger.py` | 9 | 无（纯标准库） |
| `test_optimizer.py` | 10 | 无 |
| `test_pipeline.py` | 15 | 无 |
| `test_mcp_integration.py` | 9 | mcp + uvicorn |
| `test_real_responses.py` | 18 | 无 |

集成测试会**真实启动一个 streamable-http MCP 服务器**，用麦当劳官方的连字符
工具名注册替身工具，并在服务端侧用 ASGI 中间件实测 `Authorization` 头确实送达，
而非"调用没报错就算过"。

`test_real_responses.py` 的全部样本均来自真实调用响应（含真实订单字段、真实菜单
名称与价格），已固化为回归测试。
