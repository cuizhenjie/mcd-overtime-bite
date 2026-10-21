import { Metrics } from './components/Metrics';
import { Pipeline } from './components/Pipeline';
import { LiveRun } from './components/LiveRun';
import { BackgroundField } from './components/BackgroundField';

const REPO = 'https://github.com/cuizhenjie/mcd-overtime-bite';

export default function App() {
  return (
    <>
      <BackgroundField />

      <header className="hero">
        <div className="hero-badge">
          <span className="pulse" />
          麦当劳程序员创意开发大赛 · 参赛作品
        </div>

        <h1 className="hero-title">
          Overtime<span>Bite</span>
        </h1>
        <p className="hero-sub">麦加班 · 加班晚餐自动订餐助手</p>

        <p className="hero-desc">
          你还在改第 8 版方案，胃已经在抗议了。
          <br />
          不用起身、不用打开外卖 App、不用纠结点什么——
          <b>饭会在合适的时候自己安排好</b>。
        </p>

        <div className="hero-actions">
          <a className="btn btn-primary" href={REPO} target="_blank" rel="noreferrer">
            GitHub 源码
          </a>
          <a className="btn btn-ghost" href="#run">
            看真实运行结果 ↓
          </a>
        </div>

        <div className="hero-diff">
          <table>
            <thead>
              <tr>
                <th />
                <th>省钱类项目</th>
                <th className="hl">OvertimeBite</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>起点</td><td>用户主动提问</td><td className="hl">时间自己走到点</td></tr>
              <tr><td>交付</td><td>推荐几个方案让人选</td><td className="hl">直接下单</td></tr>
              <tr><td>价格角色</td><td>优化目标</td><td className="hl">预算硬约束</td></tr>
              <tr><td>关键洞察</td><td>用券省多少</td><td className="hl">配送费占预算多少</td></tr>
              <tr><td>个性化</td><td>问用户想吃什么</td><td className="hl">从历史订单自己学</td></tr>
            </tbody>
          </table>
        </div>
      </header>

      <main className="content">
        <Metrics />
        <Pipeline />
        <LiveRun />

        <section className="section safety" id="safety">
          <div className="section-head">
            <h2>自动花钱的产品，先学会不花钱</h2>
            <p>五道闸门里只要有一道过不去，它就不下单。</p>
          </div>
          <div className="gates">
            {[
              ['时间窗口', '18:00 ~ 24:00', '窗口外静默跳过'],
              ['加班证据', '必须为真', '未知或否 → 需人工确认'],
              ['幂等', '同一 4 小时窗一次', '不会重复下单'],
              ['每日上限', '默认 1 单', '超限直接跳过'],
              ['截止时间', '22:00 后', '转人工确认'],
            ].map(([name, rule, effect]) => (
              <div className="gate" key={name}>
                <div className="gate-name">{name}</div>
                <div className="gate-rule">{rule}</div>
                <div className="gate-effect">{effect}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="section footer-cta">
          <h2>下次加班，别再纠结点什么了</h2>
          <p>核心逻辑只依赖 Python 标准库，67 个测试不用 Token 就能跑完。</p>
          <a className="btn btn-primary btn-lg" href={REPO} target="_blank" rel="noreferrer">
            看源码
          </a>
          <p className="disclaimer">
            本项目为麦当劳程序员创意开发大赛参赛作品，由参赛者独立开发，非麦当劳官方产品。
            餐品信息、价格与供应状态以麦当劳官方渠道实时结果为准。
          </p>
        </section>
      </main>
    </>
  );
}
