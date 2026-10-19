import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { nodeFragment, nodeVertex } from '../shaders/node';

/**
 * 五步决策链的 3D 节点群。
 *
 * 每个步骤是一个着色器球体，按顺序亮起——把"时间到了自动往下走"
 * 这个核心机制变成可看见的东西，而不是一行文字说明。
 */
const STEPS = 5;
const RADIUS = 2.9;

export function PipelineNodes({ paused = false }: { paused?: boolean }) {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      55,
      mount.clientWidth / mount.clientHeight,
      0.1,
      100,
    );
    camera.position.set(0, 0.4, 7.6);
    camera.lookAt(0, 0, 0);

    const group = new THREE.Group();
    scene.add(group);

    const geometry = new THREE.SphereGeometry(0.42, 48, 48);
    const meshes: THREE.Mesh[] = [];
    const uniformsList: { value: number }[] = [];

    for (let i = 0; i < STEPS; i++) {
      const angle = (i / STEPS) * Math.PI * 2 - Math.PI / 2;
      const uniforms = {
        uTime: { value: 0 },
        uActive: { value: 0 },
        uColor: { value: new THREE.Color('#22d3ee') },
        uColorDim: { value: new THREE.Color('#4a7fb5') },
      };
      const mesh = new THREE.Mesh(
        geometry,
        new THREE.ShaderMaterial({
          vertexShader: nodeVertex,
          fragmentShader: nodeFragment,
          uniforms,
          transparent: true,
        }),
      );
      mesh.position.set(Math.cos(angle) * RADIUS, Math.sin(angle) * RADIUS * 0.56, 0);
      group.add(mesh);
      meshes.push(mesh);
      uniformsList.push(uniforms.uActive);
    }

    // 中心连线：能量流在五个步骤之间传递
    const lineMaterial = new THREE.LineBasicMaterial({
      color: new THREE.Color('#22d3ee'),
      transparent: true,
      opacity: 0.34,
    });
    const points: THREE.Vector3[] = [];
    for (let i = 0; i <= STEPS; i++) {
      const angle = ((i % STEPS) / STEPS) * Math.PI * 2 - Math.PI / 2;
      points.push(
        new THREE.Vector3(
          Math.cos(angle) * RADIUS,
          Math.sin(angle) * RADIUS * 0.56,
          0,
        ),
      );
    }
    const loop = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(points),
      lineMaterial,
    );
    group.add(loop);

    const startedAt = performance.now();
    let frame = 0;
    const pointer = { x: 0, y: 0, tx: 0, ty: 0 };

    const onPointerMove = (e: PointerEvent) => {
      const rect = mount.getBoundingClientRect();
      pointer.tx = ((e.clientX - rect.left) / rect.width - 0.5) * 0.5;
      pointer.ty = ((e.clientY - rect.top) / rect.height - 0.5) * 0.3;
    };
    window.addEventListener('pointermove', onPointerMove);

    const resize = () => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (w === 0 || h === 0) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    window.addEventListener('resize', resize);

    const tick = () => {
      frame = requestAnimationFrame(tick);
      if (paused) return;
      const t = (performance.now() - startedAt) / 1000;

      pointer.x += (pointer.tx - pointer.x) * 0.05;
      pointer.y += (pointer.ty - pointer.y) * 0.05;
      group.rotation.y = pointer.x * 0.6 + t * 0.06;
      group.rotation.x = pointer.y * 0.4;
      group.position.y = Math.sin(t * 0.5) * 0.06;

      // 依次点亮：每 1.15 秒推进一个步骤，形成"自动往下走"的观感
      for (let i = 0; i < STEPS; i++) {
        const phase = ((t / 1.15) % STEPS) - i;
        const active = Math.max(0, 1 - Math.abs(phase)) ;
        uniformsList[i].value += (active - uniformsList[i].value) * 0.12;
        const s = 1 + uniformsList[i].value * 0.22;
        meshes[i].scale.setScalar(s);
      }

      lineMaterial.opacity = 0.30 + Math.sin(t * 1.4) * 0.09;

      renderer.render(scene, camera);
    };
    tick();

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onPointerMove);
      geometry.dispose();
      lineMaterial.dispose();
      meshes.forEach((m) => (m.material as THREE.Material).dispose());
      loop.geometry.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [paused]);

  return <div ref={mountRef} className="pipeline-canvas" aria-hidden="true" />;
}
