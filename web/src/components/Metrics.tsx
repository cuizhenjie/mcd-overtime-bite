const METRICS = [
  { value: '67', label: '单元测试', sub: '不用 Token 就能跑完' },
  { value: '13', label: 'MCP Tool', sub: '每个都在真服务上调过' },
  { value: '¥30', label: '预算上限', sub: '超了就不下单' },
  { value: '0', label: '第三方依赖', sub: '核心逻辑只用标准库' },
];

export function Metrics() {
  return (
    <section className="section metrics-section" id="metrics">
      {METRICS.map((m) => (
        <div className="metric" key={m.label}>
          <div className="metric-value">{m.value}</div>
          <div className="metric-label">{m.label}</div>
          <div className="metric-sub">{m.sub}</div>
        </div>
      ))}
    </section>
  );
}
