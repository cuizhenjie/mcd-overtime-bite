const METRICS = [
  { value: '67', label: '单元测试', sub: '全部离线可跑' },
  { value: '11', label: 'MCP Tool', sub: '真实服务核对过参数' },
  { value: '¥30', label: '预算硬上限', sub: '永不超支' },
  { value: '0', label: '运行时依赖', sub: '核心逻辑仅标准库' },
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
