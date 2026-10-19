# X（Twitter）短文

## 主推帖

```
做了个 MCP 点麦当劳的助手，到点自己点。

省钱的项目这赛道已经一堆了，所以我把入口放在「时间」：加班到点自动领券、选渠道、凑组合、算价、下单，30 元预算硬约束。

真实账户跑出来最优解 ¥16.90，比预算省 ¥13.10。

3D 产品页（能看真实调用轨迹）：
https://lq63qr8tasyc1.space.mcode.cn

GitHub 67 测试全绿，MIT：
https://github.com/cuizhenjie/mcd-overtime-bite

#MCP #麦当劳程序员创意开发大赛
```

---

## 续帖 1/3（技术向，吸引开发者）

```
顺带扒出了麦当劳 MCP 的 8 个 API 坑，都写进 README 了，前一半：

1. ClientSession 在第一次调用前就死了（anyio task group 被 cancel）
2. mcp 2.x 掉了 Authorization 头 → 直接 401，必须锁 <2.0
3. 同一个服务器三种返回形态，业务错误信号是 success:false 不是 isError
4. 工具参数名全错，order-list 还不接受任何参数
```

## 续帖 2/3（技术向，后一半）

```
后一半：

5. 历史订单字段全错，套餐还多一层嵌套
6. 菜单没有价格，价格只在 calculate-price 里（单位：分）
7. 券后价藏在券标题里：「11.9元麦辣鸡腿堡」原价 22.5
8. 菜单里混着「按摩捶痒痒挠 ¥14.90」
```

## 续帖 3/3（安全设计 + CTA）

```
做自动花钱的产品，第一优先级不是聪明，是克制。

五道闸：时间窗口 / 加班证据 / 幂等 / 每日上限 / 22:00 截止，任一不过就降级人工确认。dry_run 默认开。

加班证据「未知」和确认「没在加班」都走同一条路：降级确认。

宁可不点，也不误点。

源码 https://github.com/cuizhenjie/mcd-overtime-bite
```

---

## 通用短版（朋友圈 / 群发 / 一句话引流）

```
加班到点，饭自己安排好。

OvertimeBite · 麦加班 —— 麦当劳 MCP 参赛作品
按时间 + 位置 + 你的历史口味 + 30 元预算上限，自动领券、选渠道、凑组合、算价、下单。

3D 产品页：https://lq63qr8tasyc1.space.mcode.cn
GitHub（MIT，67 测试全绿）：https://github.com/cuizhenjie/mcd-overtime-bite
```

---

## 发布提示

- **X**：主推帖正文用上面的「主推帖」原样复制；两条续帖在主推帖发出后作为 thread 回复。视频可直接上传（10s / 1344×768 / H.264），配文用主推帖。
- **掘金**：正文用 `juejin.md`。掘金支持本地图片，发布时把 `assets/` 下的 `cover.png`、`page-pipeline.png`、`page-hero.png` 分别上传并替换对应图片链接；视频用外链或单独上传。
- **V2EX**：正文用 `v2ex.md`，图片需用图床或 V2EX 图床直链。节点建议「创造」或「分享」。
- 三版统一口径：**不刷 Star、不自点**，靠自然传播。
