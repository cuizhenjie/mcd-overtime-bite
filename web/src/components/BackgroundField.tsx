import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { backgroundFragment, backgroundVertex } from '../shaders/background';

/**
 * 全屏背景场。
 *
 * 用单个全屏四边形 + 片元着色器，而不是铺一堆 3D 物体：
 * 画面复杂度全在 shader 里，CPU 侧只有一次 draw call，移动端也稳。
 */
export function BackgroundField({ paused = false }: { paused?: boolean }) {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const renderer = new THREE.WebGLRenderer({
      antialias: false,
      powerPreference: 'high-performance',
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.domElement.style.position = 'fixed';
    renderer.domElement.style.inset = '0';
    renderer.domElement.style.zIndex = '0';
    renderer.domElement.style.display = 'block';
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.Camera();

    const uniforms = {
      uTime: { value: 0 },
      uResolution: { value: new THREE.Vector2(window.innerWidth, window.innerHeight) },
      uColorA: { value: new THREE.Color('#22d3ee') },
      uColorB: { value: new THREE.Color('#8b5cf6') },
      uIntensity: { value: 1 },
    };

    const plane = new THREE.Mesh(
      new THREE.PlaneGeometry(2, 2),
      new THREE.ShaderMaterial({
        vertexShader: backgroundVertex,
        fragmentShader: backgroundFragment,
        uniforms,
        depthWrite: false,
        depthTest: false,
      }),
    );
    scene.add(plane);

    const startedAt = performance.now();
    let frame = 0;

    const resize = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      renderer.setSize(w, h);
      uniforms.uResolution.value.set(w, h);
    };
    window.addEventListener('resize', resize);

    const tick = () => {
      frame = requestAnimationFrame(tick);
      if (paused) return;
      uniforms.uTime.value = (performance.now() - startedAt) / 1000;
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
      plane.geometry.dispose();
      (plane.material as THREE.Material).dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [paused]);

  return <div ref={mountRef} className="bg-field" aria-hidden="true" />;
}
