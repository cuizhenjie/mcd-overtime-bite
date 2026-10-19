/**
 * 决策链节点着色器：一个悬浮的"能量环 + 内核"。
 *
 * 为什么单独一个组件而不是 CSS 动画：
 * - 环的能量流动要和背景的 uTime 保持同一时钟，视觉上才像同一个系统
 * - 片元着色器能做 CSS 做不到的径向干涉条纹，成本却只有一个 draw call
 */

export const nodeVertex = /* glsl */ `
varying vec3 vNormal;
varying vec3 vViewPosition;
varying vec2 vUv;

void main() {
  vUv = uv;
  vNormal = normalize(normalMatrix * normal);
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  vViewPosition = -mvPosition.xyz;
  gl_Position = projectionMatrix * mvPosition;
}
`;

export const nodeFragment = /* glsl */ `
precision highp float;

varying vec3 vNormal;
varying vec3 vViewPosition;
varying vec2 vUv;

uniform float uTime;
uniform float uActive;     // 0..1，该节点当前是否高亮
uniform vec3  uColor;
uniform vec3  uColorDim;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

void main() {
  vec3 normal = normalize(vNormal);
  vec3 viewDir = normalize(vViewPosition);

  // 菲涅尔：边缘亮、正面暗，做出全息膜的感觉
  float fresnel = pow(1.0 - clamp(dot(normal, viewDir), 0.0, 1.0), 2.0);

  // 沿纬度流动的能量条纹
  float bands = sin((vUv.y * 34.0) - uTime * 2.4) * 0.5 + 0.5;
  bands = pow(bands, 3.0);

  float base = mix(0.34, 0.62, uActive);
  vec3 color = mix(uColorDim, uColor, uActive) * (base + fresnel * 1.35);
  color += uColor * bands * (0.10 + 0.35 * uActive);

  // 轻微的横向扫描，模拟全息投影的刷新
  float scan = sin(vUv.y * 180.0) * 0.5 + 0.5;
  color *= 0.92 + 0.08 * scan;

  // 稀疏噪点，避免大面积渐变出现色带
  color += (hash(gl_FragCoord.xy + uTime) - 0.5) * 0.02;

  gl_FragColor = vec4(color, 1.0);
}
`;
