/**
 * 科幻背景场的片元着色器。
 *
 * 设计目标：深夜控制台感 + 全息 HUD，而不是常见的霓虹赛博朋克套模板。
 * 分层叠加（从后到前）：
 *   1. 深空底色渐变（带轻微色相漂移，避免死板的纯黑）
 *   2. fbm 噪声星云 —— 提供纵深与"能量场"质感
 *   3. 透视网格地平线 —— 全息台的标志性元素
 *   4. 扫描线 + 暗角 —— CRT 质感，压住画面边缘
 *
 * 刻意不用 raymarching：单层 fbm + 网格在移动端也能跑满帧，
 * 而这个页面要能截图发社区，加载必须快。
 */

export const backgroundVertex = /* glsl */ `
varying vec2 vUv;
varying vec3 vPosition;

void main() {
  vUv = uv;
  vPosition = position;
  gl_Position = vec4(position, 1.0);
}
`;

export const backgroundFragment = /* glsl */ `
precision highp float;

varying vec2 vUv;
varying vec3 vPosition;

uniform float uTime;
uniform vec2  uResolution;
uniform vec3  uColorA;   // 青 —— 主色
uniform vec3  uColorB;   // 紫 —— 次色
uniform float uIntensity;

/* ---------- 工具函数 ---------- */

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}

/* 五层 fbm，旋转叠加避免出现明显的网格轴向 */
float fbm(vec2 p) {
  float value = 0.0;
  float amplitude = 0.5;
  mat2 rot = mat2(0.80, 0.60, -0.60, 0.80);
  for (int i = 0; i < 5; i++) {
    value += amplitude * noise(p);
    p = rot * p * 2.02;
    amplitude *= 0.5;
  }
  return value;
}

/* 透视网格：把 uv 映射到一个向后延伸的地面 */
float gridMask(vec2 uv, float scale, float thickness) {
  vec2 g = abs(fract(uv * scale - 0.5) - 0.5) / fwidth(uv * scale);
  float line = min(g.x, g.y);
  return 1.0 - min(line / thickness, 1.0);
}

void main() {
  vec2 uv = vUv;
  vec2 p = (uv - 0.5) * vec2(uResolution.x / uResolution.y, 1.0);

  float t = uTime;

  /* ---------- 1. 底色 ---------- */
  vec3 color = mix(vec3(0.016, 0.020, 0.043), vec3(0.035, 0.016, 0.063), uv.y);
  color += 0.012 * vec3(uColorB) * (0.5 + 0.5 * sin(t * 0.15));

  /* ---------- 2. 星云 ---------- */
  vec2 q = p * 1.35;
  float n = fbm(q + vec2(t * 0.035, -t * 0.021));
  float n2 = fbm(q * 1.9 - vec2(t * 0.017, t * 0.026));
  float nebula = pow(n * 0.75 + n2 * 0.45, 1.9);

  vec3 nebulaColor = mix(uColorB, uColorA, clamp(n * 1.25, 0.0, 1.0));
  color += nebulaColor * nebula * 0.34;

  /* 稀疏星点：只有高频噪声接近 1 时才亮，避免糊成噪点 */
  float stars = smoothstep(0.985, 1.0, noise(p * 210.0 + t * 0.6));
  color += vec3(0.85, 0.95, 1.0) * stars * 0.85;

  /* ---------- 3. 透视地平线 ---------- */
  // 地平线固定在下半部；上半部做一层稀疏的"数据网格"天花板
  float horizon = 0.34;
  float below = smoothstep(horizon + 0.02, horizon - 0.02, uv.y);

  if (below > 0.001) {
    float depth = (horizon - uv.y) / horizon;      // 0 在地平线，1 在底部
    float z = 1.0 / max(depth, 0.02);
    vec2 gridUv = vec2(p.x * 1.4, z * 0.55 - t * 0.55);
    float lines = gridMask(gridUv, 7.0, 1.1);
    float fade = smoothstep(0.0, 0.35, depth) * (1.0 - smoothstep(0.55, 1.0, depth));
    color += uColorA * lines * fade * 0.42 * below;
  }

  // 天花板网格：更淡、更远
  float above = smoothstep(0.62, 0.80, uv.y);
  if (above > 0.001) {
    float depth = (uv.y - 0.62) / 0.38;
    float z = 1.0 / max(depth, 0.02);
    vec2 gridUv = vec2(p.x * 0.9, z * 0.35 + t * 0.22);
    float lines = gridMask(gridUv, 5.0, 1.0);
    float fade = (1.0 - smoothstep(0.1, 1.0, depth)) * 0.5;
    color += uColorB * lines * fade * 0.30 * above;
  }

  /* ---------- 4. 地平线光带 ---------- */
  float beam = exp(-abs(uv.y - horizon) * 46.0);
  color += uColorA * beam * 0.30;

  /* ---------- 5. 扫描线 + 暗角 ---------- */
  float scan = sin(uv.y * uResolution.y * 1.35) * 0.5 + 0.5;
  color *= 1.0 - 0.045 * scan;

  vec2 centered = uv - 0.5;
  float vignette = 1.0 - dot(centered, centered) * 1.05;
  color *= clamp(vignette, 0.0, 1.0);

  color *= uIntensity;

  gl_FragColor = vec4(color, 1.0);
}
`;
