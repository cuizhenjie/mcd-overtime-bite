import { PipelineNodes } from './PipelineNodes';

const STEPS = [
  {
    title: '该点了吗',
    detail: '时间窗口 + 加班证据 + 幂等 + 每日上限，五道安全闸',
    note: '任一不通过就转人工确认',
  },
  {
    title: '你在附近哪',
    detail: '城市 + 关键词定位门店，带回距离、步行时间、配送费、ETA',
    note: '真实接口收 city+keyword+beType，不收地址',
  },
  {
    title: '你爱吃什么',
    detail: '从 order-list 历史订单实时学习品类偏好',
    note: '套餐按内层单品计算：「没点过」≠「讨厌」',
  },
  {
    title: '怎么拿',
    detail: '外送 vs 到店自取，先定渠道再选商品',
    note: '配送费吃掉预算 20% 时，改走自取',
  },
  {
    title: '吃什么',
    detail: '预算硬约束下的组合寻优 + 叠券 + 解释排序',
    note: '找不到可行解就返回空，不为了凑数而超预算',
  },
];

export function Pipeline() {
  return (
    <section className="section" id="pipeline">
      <div className="section-head">
        <h2>五步决策链</h2>
        <p>这五步里，只有第一步可能会来问你一句。剩下的它自己跑完。</p>
      </div>

      <div className="pipeline-wrap">
        <PipelineNodes />

        <ol className="pipeline-list">
          {STEPS.map((s, i) => (
            <li key={s.title} className="step" style={{ ['--i' as string]: i }}>
              <div className="step-index">{String(i + 1).padStart(2, '0')}</div>
              <div className="step-body">
                <h3>{s.title}</h3>
                <p className="step-detail">{s.detail}</p>
                <p className="step-note">{s.note}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
