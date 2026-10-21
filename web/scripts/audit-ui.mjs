/**
 * OvertimeBite 产品页 UI 审计脚本（零运行时依赖，直连 Chrome DevTools Protocol）
 *
 * 做两件事：
 *   1) 对比度实算 —— 用视口截图的**真实像素**当背景，Range 精确定位文字像素、
 *      取众数排除字形本身，算出实际对比度。不靠 CSS 理论值，也不靠 rgba 叠加推算。
 *   2) 同质化信号量 —— 统计居中、渐变文字、胶囊圆角、阴影等「一眼 AI 感」的量。
 *
 * 为什么需要它：这个页面的背景是 WebGL 实时渲染的，亮网格线会从文字背后穿过，
 * 静态 CSS 取色算不出真实对比度。必须按实际渲染结果逐屏实测。
 *
 * 用法：
 *   # 1) 起一个带调试端口的 Chrome
 *   #    /Applications/Google Chrome.app/Contents/MacOS/Google Chrome \
 *   #      --headless=new --remote-debugging-port=9223 \
 *   #      --user-data-dir=/tmp/ob-chrome --window-size=1440,900 about:blank
 *   # 2) 跑审计
 *   node scripts/audit-ui.mjs http://localhost:4173/ 9223
 *
 * 退出码：0 = 对比度全部达标；1 = 有不达标项。
 */

import { writeFileSync, mkdirSync } from 'node:fs';

const URL_TO_AUDIT = process.argv[2] || 'http://localhost:4173/';
const PORT = Number(process.argv[3] || 9223);
const REPORT_PATH = process.argv[4] || 'audit-report.txt';

const VIEWPORTS = [
  { w: 1440, h: 900, mobile: false, label: '桌面 1440' },
  { w: 390, h: 844, mobile: true, label: '移动 390' },
];

// ---------- 极简 CDP 客户端（Node 内置 WebSocket，零依赖） ----------

class CDP {
  constructor(ws) { this.ws = ws; this.seq = 0; this.pending = new Map(); }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error('WebSocket 连接失败: ' + url));
    });
    const cdp = new CDP(ws);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && cdp.pending.has(msg.id)) {
        const { resolve, reject } = cdp.pending.get(msg.id);
        cdp.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    };
    return cdp;
  }
  send(method, params = {}) {
    const id = ++this.seq;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Chrome 可能绑 IPv6 ::1，也可能绑 IPv4 127.0.0.1，两个都试 */
async function pickHost(port) {
  for (const h of ['[::1]', '127.0.0.1']) {
    try {
      const r = await fetch(`http://${h}:${port}/json/version`);
      if (r.ok) return h;
    } catch { /* 换下一个 */ }
  }
  throw new Error('CDP 端点不可达（端口 ' + port + '）');
}

// ---------- 页面内审计函数 ----------

const AUDIT_SRC = `
window.__obAudit = async function (b64) {
  const img = new Image();
  img.src = 'data:image/png;base64,' + b64;
  await img.decode();                       // 图片加载是异步的，必须等解码完成
  const cv = document.createElement('canvas');
  const ctx = cv.getContext('2d', { willReadFrequently: true });

  const W = img.width, H = img.height;
  cv.width = W; cv.height = H;
  ctx.drawImage(img, 0, 0);
  const data = ctx.getImageData(0, 0, W, H).data;

  const px = (x, y) => {
    const i = (Math.round(y) * W + Math.round(x)) * 4;
    return [data[i], data[i + 1], data[i + 2]];
  };

  const lum = (c) => {
    const f = c.map((v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  };
  const parseColor = (s) => {
    const m = s.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(',').map((v) => parseFloat(v));
    return [p[0], p[1], p[2]];
  };

  const dpr = window.devicePixelRatio || 1;
  const results = [];
  const seen = new Set();

  for (const el of document.querySelectorAll('body *')) {
    if (el.children.length > 0) continue;                 // 只看叶子节点上的文字
    const text = (el.textContent || '').trim();
    if (!text) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) < 0.1) continue;

    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = Array.from(range.getClientRects()).filter((r) => r.width > 1 && r.height > 1);
    if (!rects.length) continue;
    const r = rects[0];

    const fg = parseColor(cs.color);
    if (!fg) continue;

    // 在文字 rect 内采样，剔除接近前景色的像素（那是字形本身），取众数作背景
    const x0 = Math.max(0, Math.floor(r.left * dpr));
    const x1 = Math.min(W - 1, Math.ceil(r.right * dpr));
    const y0 = Math.max(0, Math.floor(r.top * dpr));
    const y1 = Math.min(H - 1, Math.ceil(r.bottom * dpr));
    if (x1 <= x0 || y1 <= y0) continue;

    const buckets = new Map();
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const c = px(x, y);
        const d = Math.abs(c[0] - fg[0]) + Math.abs(c[1] - fg[1]) + Math.abs(c[2] - fg[2]);
        if (d < 70) continue;
        const key = ((c[0] >> 4) << 8) | ((c[1] >> 4) << 4) | (c[2] >> 4);
        buckets.set(key, (buckets.get(key) || 0) + 1);
      }
    }
    if (!buckets.size) continue;
    let bestKey = null, bestN = -1;
    for (const [k, n] of buckets) if (n > bestN) { bestN = n; bestKey = k; }
    const bg = [((bestKey >> 8) & 15) * 17, ((bestKey >> 4) & 15) * 17, (bestKey & 15) * 17];

    const size = parseFloat(cs.fontSize);
    const weight = parseInt(cs.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = large ? 3 : 4.5;
    const cr = ratio(fg, bg);

    const key = cs.color + '|' + text.slice(0, 24) + '|' + el.className;
    if (seen.has(key)) continue;
    seen.add(key);

    results.push({
      cls: (el.className || el.tagName).toString().slice(0, 42),
      text: text.slice(0, 26),
      size, weight,
      color: cs.color,
      bg: 'rgb(' + bg.join(',') + ')',
      ratio: Math.round(cr * 100) / 100,
      need,
      pass: cr >= need,
    });
  }

  // ---- 同质化信号量 ----
  const all = Array.from(document.querySelectorAll('body *'));
  const centered = all.filter((el) => getComputedStyle(el).textAlign === 'center').length;
  const gradText = all.filter((el) => {
    const cs = getComputedStyle(el);
    return (cs.webkitBackgroundClip === 'text' || cs.backgroundClip === 'text') &&
           cs.webkitTextFillColor === 'rgba(0, 0, 0, 0)';
  }).length;
  const pill = all.filter((el) => parseFloat(getComputedStyle(el).borderRadius) >= 99).length;
  const shadowed = all.filter((el) => getComputedStyle(el).boxShadow !== 'none').length;
  const radius = {};
  for (const el of all) {
    const v = Math.round(parseFloat(getComputedStyle(el).borderRadius));
    if (!v) continue;
    radius[v] = (radius[v] || 0) + 1;
  }

  return {
    total: results.length,
    failed: results.filter((r) => !r.pass),
    signals: { centered, gradText, pill, shadowed, radius, elements: all.length },
  };
};
`;

// ---------- 主流程 ----------

async function main() {
  const host = await pickHost(PORT);
  const list = await (await fetch(`http://${host}:${PORT}/json/list`)).json();
  let target = list.find((t) => t.type === 'page');
  if (!target) {
    target = await (await fetch(`http://${host}:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
  }

  const cdp = await CDP.connect(target.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  const all = [];
  let signals = null;
  let screens = 0;

  for (const vp of VIEWPORTS) {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: vp.w, height: vp.h, deviceScaleFactor: 1, mobile: vp.mobile,
    });
    await cdp.send('Page.navigate', { url: URL_TO_AUDIT });
    await sleep(4000);
    await cdp.send('Runtime.evaluate', { expression: AUDIT_SRC });

    const h = await cdp.send('Runtime.evaluate', {
      expression: 'document.body.scrollHeight', returnByValue: true,
    });
    const docH = h.result.value;
    const count = Math.min(10, Math.ceil(docH / vp.h));
    screens += count;

    for (let i = 0; i < count; i++) {
      await cdp.send('Runtime.evaluate', {
        expression: `window.scrollTo({top:${i * vp.h},behavior:'instant'})`, returnByValue: true,
      });
      await sleep(1100);
      const shot = await cdp.send('Page.captureScreenshot', { format: 'png' });
      const out = await cdp.send('Runtime.evaluate', {
        expression: `window.__obAudit(${JSON.stringify(shot.data)})`,
        awaitPromise: true,
        returnByValue: true,
      });
      if (out.exceptionDetails) {
        throw new Error('页面内审计异常: ' + JSON.stringify(out.exceptionDetails).slice(0, 400));
      }
      const parsed = out.result.value;
      for (const f of parsed.failed) { f.vp = vp.label; all.push(f); }
      signals = parsed.signals;
    }
  }

  const seen = new Set();
  const failed = all.filter((f) => {
    const k = f.vp + '|' + f.cls + '|' + f.text;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });

  const lines = [];
  lines.push('OvertimeBite 产品页 UI 审计');
  lines.push('URL: ' + URL_TO_AUDIT);
  lines.push('断点: ' + VIEWPORTS.map((v) => v.label).join(' / ') + '，共 ' + screens + ' 屏');
  lines.push('');
  lines.push('=== 对比度不达标 ===');
  if (!failed.length) lines.push('  全部通过 ✓');
  for (const f of failed) {
    lines.push(`  [${f.ratio} / 需 ${f.need}] ${f.vp} ${f.size}px ${f.cls} | "${f.text}" | ${f.color} on ${f.bg}`);
  }
  lines.push('');
  lines.push('=== 同质化信号量 ===');
  lines.push(JSON.stringify(signals, null, 2));

  const text = lines.join('\n');
  console.log(text);
  const reportDir = REPORT_PATH.includes('/') ? REPORT_PATH.replace(/\/[^/]+$/, '') : '.';
  mkdirSync(reportDir, { recursive: true });
  writeFileSync(REPORT_PATH, text + '\n');
  console.log('\n报告已写入 ' + REPORT_PATH);

  cdp.ws.close();
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => { console.error('失败:', e.message); process.exit(2); });
