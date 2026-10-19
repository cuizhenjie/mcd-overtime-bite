import { Terminal } from './Terminal';

/** 真实跑一次管线抓下来的输出，不是编的。 */
const RUN_OUTPUT = [
  '> overtime-bite · 2026-10-19 18:13 · 北京',
  '',
  '· 当前时间：2026-10-19 18:13',
  '· 触发判定：confirm —— 配置为不自动下单，需人工确认',
  '· 选定门店：麦当劳北京马家堡路餐厅（467m，配送费 ¥0.00）',
  '· 口味画像已更新（基于 9 笔真实历史订单）',
  '· 菜单载入 115 个在售餐品（已剔除酱料/周边等非正餐条目）',
  '· 可用券 3 张，其中 2 个商品命中券后价',
  '· 渠道决策：pickup —— 门店不支持外送，步行 6 分钟到店取餐',
  '· 寻优完成，3 个候选方案，最高分 0.779',
  '· 已用 calculate-price 向麦当劳侧复核金额',
  '',
  '麦辣鸡腿汉堡(原价¥22.5→券后¥11.9) + 灰焰圆筒',
  '到店取餐（免配送费）｜合计 ¥16.90',
  '  · 饱腹度高（1.00），加班到深夜不容易饿醒',
  '  · 含可用券商品：11.9元麦辣鸡腿堡',
  '  · 预算友好：比 ¥30 上限省 ¥13.10',
];

export function LiveRun() {
  return (
    <section className="section" id="run">
      <div className="section-head">
        <h2>这是它上一轮的真实输出</h2>
        <p>
          连麦当劳 MCP 真服务器跑出来的，不是示意图。每一个数字都能追溯到一次具体调用。
        </p>
      </div>

      <div className="run-grid">
        <Terminal lines={RUN_OUTPUT} />

        <div className="run-notes">
          <div className="note-card">
            <h3>¥22.5 → ¥11.9</h3>
            <p>
              券的定价藏在标题里。「11.9元麦辣鸡腿堡」开头就是券后实付价，
              该商品原价 ¥22.5。接口只给了 <code>productCode</code>，
              解析标题才拿得到——否则寻优器会嫌贵直接放弃这张券。
            </p>
          </div>
          <div className="note-card">
            <h3>省 ¥13.10</h3>
            <p>
              评分里原本有一项算的是 <code>total/subtotal</code> 比值，
              在无配送费的到店场景下恒等于 1，于是花 ¥17 和花 ¥24 得分一样。
              改成按「预算内省下多少」计分后，同场景最优方案从 ¥24 降到 ¥16.90。
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
