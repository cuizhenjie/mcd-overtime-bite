import { useEffect, useRef, useState } from 'react';

/** 逐行打字的终端输出。用真实抓取的数据，不用编造的。 */
export function Terminal({ lines, speed = 34 }: { lines: string[]; speed?: number }) {
  const [shown, setShown] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (shown >= lines.length) return;
    const timer = setTimeout(() => setShown((n) => n + 1), speed);
    return () => clearTimeout(timer);
  }, [shown, lines.length, speed]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [shown]);

  return (
    <div className="terminal" role="log" aria-live="polite">
      <div className="terminal-bar">
        <span className="dot" /><span className="dot" /><span className="dot" />
        <span className="terminal-title">overtime-bite · live run · 真实调用轨迹</span>
      </div>
      <pre className="terminal-body">
        {lines.slice(0, shown).map((line, i) => (
          <div key={i} className={line.startsWith('·') ? 'l-trace' : 'l-out'}>
            {line}
          </div>
        ))}
        {shown < lines.length && <span className="caret" />}
      </pre>
      <div ref={endRef} />
    </div>
  );
}
