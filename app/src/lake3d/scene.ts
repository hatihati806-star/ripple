import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { ReliefGrid, ReliefSurface } from "./relief";

declare global {
  interface Window {
    /**
     * Introspection hook for the headless 3D verifier
     * (rocky/tools/web_3d_verifier.py). Present only while the 3D view is mounted.
     */
    __ROCKY_THREE_SCENE__?: {
      renderer: THREE.WebGLRenderer;
      scene: THREE.Scene;
      camera: THREE.PerspectiveCamera;
    };
  }
}

export interface LakePick {
  col: number;
  row: number;
  risk: number;
  valid: boolean;
}

export interface LakeUpdate {
  surface: ReliefSurface;
  relief: ReliefGrid;
  extentXKm: number;
  extentZKm: number;
  /** The height in km that a risk of 1.0 was scaled to. */
  heightKm: number;
  /** Move the camera to frame this surface; false keeps the user's orbit. */
  recenter: boolean;
}

export interface LakeSceneHandle {
  update(data: LakeUpdate): void;
  setExaggeration(multiplier: number): void;
  /** Show a marker at a picked cell, or hide it with null. */
  setMarker(pick: LakePick | null): void;
  /** NDC coordinates in [-1, 1]; returns the nearest surface cell, or null. */
  pick(ndcX: number, ndcY: number): LakePick | null;
  setSize(width: number, height: number): void;
  dispose(): void;
}

/**
 * The 3D lake scene.
 *
 * One indexed mesh carries the lake: x/z are true ground km, y is the decimated risk, and
 * the vertex colours are the map ramp in linear space. A translucent plate sits at y < 0
 * as "paper" so the relief reads as a model of the lake rather than a floating shell, and
 * a small marker can be dropped on any picked cell.
 *
 * Everything is deliberately unsmoothed across no-data pixels: grey vertices keep height 0
 * and the no-observation colour, so a cloudy week looks flat and grey in 3D for the same
 * reason it looks grey on the map.
 */
export function createLakeScene(canvas: HTMLCanvasElement): LakeSceneHandle {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.2, 40000);
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    preserveDrawingBuffer: true,
  });
  renderer.setPixelRatio(Math.min(typeof window === "undefined" ? 1 : window.devicePixelRatio || 1, 2));
  renderer.debug.checkShaderErrors = true;

  const hemi = new THREE.HemisphereLight(0xffffff, 0x9aa8bb, 1.5);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xffffff, 1.9);
  scene.add(sun);

  const plate = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({ color: 0xdbe3ee, transparent: true, opacity: 0.85 }),
  );
  plate.rotation.x = -Math.PI / 2;
  plate.name = "base-plate";
  scene.add(plate);

  const markerMaterial = new THREE.MeshBasicMaterial({ color: 0x0f172a });
  const marker = new THREE.Group();
  marker.name = "pick-marker";
  const cone = new THREE.Mesh(new THREE.ConeGeometry(0.5, 1.5, 12), markerMaterial);
  cone.rotation.x = Math.PI;
  const ball = new THREE.Mesh(new THREE.SphereGeometry(0.42, 12, 8), markerMaterial);
  ball.position.y = 1.05;
  marker.add(cone, ball);
  marker.visible = false;
  scene.add(marker);

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.49;
  controls.enablePan = true;

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  let waterMesh: THREE.Mesh | null = null;
  let grid: ReliefGrid | null = null;
  let extents = { x: 100, z: 100 };
  let heightKm = 10;
  let exaggeration = 1;
  let markerBase: { x: number; y: number; z: number } | null = null;
  let disposed = false;

  function placeMarker(): void {
    if (!markerBase) {
      marker.visible = false;
      return;
    }
    marker.position.set(markerBase.x, markerBase.y * exaggeration, markerBase.z);
    marker.visible = true;
  }

  function disposeWater(): void {
    if (!waterMesh) return;
    scene.remove(waterMesh);
    waterMesh.geometry.dispose();
    (waterMesh.material as THREE.Material).dispose();
    waterMesh = null;
  }

  function update(data: LakeUpdate): void {
    if (disposed) return;
    grid = data.relief;
    extents = { x: Math.max(1, data.extentXKm), z: Math.max(1, data.extentZKm) };
    heightKm = Number.isFinite(data.heightKm) ? data.heightKm : 0;

    const maxExtent = Math.max(extents.x, extents.z);
    const minExtent = Math.min(extents.x, extents.z);

    plate.scale.set(extents.x * 1.18, extents.z * 1.18, 1);
    plate.position.y = -0.05 * minExtent;
    sun.position.set(0.55 * maxExtent, 1.2 * maxExtent, 0.45 * maxExtent);

    disposeWater();
    if (data.surface.indices.length > 0) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(data.surface.positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(data.surface.colors, 3));
      geometry.setIndex(new THREE.BufferAttribute(data.surface.indices, 1));
      geometry.computeVertexNormals();
      geometry.computeBoundingSphere();
      const material = new THREE.MeshLambertMaterial({ vertexColors: true });
      waterMesh = new THREE.Mesh(geometry, material);
      waterMesh.name = "lake-surface";
      waterMesh.scale.y = exaggeration;
      scene.add(waterMesh);
    }

    marker.scale.setScalar(0.012 * maxExtent);
    placeMarker();

    controls.minDistance = 0.35 * maxExtent;
    controls.maxDistance = 6 * maxExtent;
    if (data.recenter) {
      const distance = 1.9 * maxExtent;
      camera.position.set(0.42 * distance, 0.62 * distance, 0.72 * distance);
      controls.target.set(0, heightKm * 0.25, 0);
      controls.update();
    }
  }

  function setExaggeration(multiplier: number): void {
    exaggeration = Math.min(8, Math.max(0.25, multiplier));
    if (waterMesh) waterMesh.scale.y = exaggeration;
    placeMarker();
  }

  function setMarker(pick: LakePick | null): void {
    if (!pick || !grid || pick.valid !== true) {
      markerBase = null;
      placeMarker();
      return;
    }
    const width = Math.max(1, grid.width - 1);
    const height = Math.max(1, grid.height - 1);
    const col = Math.min(grid.width - 1, Math.max(0, pick.col));
    const row = Math.min(grid.height - 1, Math.max(0, pick.row));
    const index = row * grid.width + col;
    markerBase = {
      x: (col / width - 0.5) * extents.x,
      y: grid.valid[index] === 1 ? grid.risk[index] * heightKm : 0,
      z: (row / height - 0.5) * extents.z,
    };
    placeMarker();
  }

  function pick(ndcX: number, ndcY: number): LakePick | null {
    if (!waterMesh || !grid) return null;
    pointer.set(ndcX, ndcY);
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObject(waterMesh, false);
    if (hits.length === 0) return null;
    const hit = hits[0];
    const face = hit.face;
    if (!face) return null;

    const positions = waterMesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    const array = positions.array as Float32Array;
    let best = face.a;
    let bestDistance = Infinity;
    for (const vertex of [face.a, face.b, face.c]) {
      const dx = array[vertex * 3] - hit.point.x;
      const dz = array[vertex * 3 + 2] - hit.point.z;
      const distance = dx * dx + dz * dz;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = vertex;
      }
    }
    const col = best % grid.width;
    const row = Math.floor(best / grid.width);
    return { col, row, risk: grid.risk[best], valid: grid.valid[best] === 1 };
  }

  function setSize(width: number, height: number): void {
    if (width <= 0 || height <= 0) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  let frameHandle = 0;
  function animate(): void {
    frameHandle = window.requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }

  window.__ROCKY_THREE_SCENE__ = { renderer, scene, camera };
  setSize(canvas.clientWidth || 800, canvas.clientHeight || 600);
  animate();

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    window.cancelAnimationFrame(frameHandle);
    controls.dispose();
    disposeWater();
    plate.geometry.dispose();
    (plate.material as THREE.Material).dispose();
    cone.geometry.dispose();
    ball.geometry.dispose();
    markerMaterial.dispose();
    renderer.dispose();
    try {
      renderer.forceContextLoss();
    } catch {
      // Already lost; nothing to release.
    }
    delete window.__ROCKY_THREE_SCENE__;
  }

  return { update, setExaggeration, setMarker, pick, setSize, dispose };
}
