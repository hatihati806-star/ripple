import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Frame, Manifest, WaterBody } from "../domain/types";
import { RISK_GRADIENT, riskBand } from "../domain/palette";
import { mercGridOf, pixelToLonLat } from "../map/projection";
import { formatPercent, formatRisk, riskAtFrame } from "../water/ranking";
import type { PreloadProgress } from "../data/frames";
import { Timeline } from "../timeline/Timeline";
import { createLakeScene, type LakePick, type LakeSceneHandle } from "./scene";
import { loadSurface, type SurfaceData } from "./tileSurface";

const EXAGGERATIONS = [1, 2, 4] as const;
const CACHE_LIMIT = 6;

interface Lake3DOverlayProps {
  manifest: Manifest;
  body: WaterBody;
  frame: Frame;
  frames: Frame[];
  index: number;
  playing: boolean;
  speed: number;
  preload: PreloadProgress;
  onIndex: (index: number) => void;
  onToggle: () => void;
  onSpeed: (speed: number) => void;
  onClose: () => void;
}

interface Readout {
  risk: number | null;
  lon: number;
  lat: number;
}

/**
 * Full-screen 3D analysis of one water body.
 *
 * The relief is decoded from the *same* tile the map is showing, so switching frames here
 * is switching frames there: the bloom season plays as terrain. Height and colour encode
 * one quantity (the relative risk index) -- nothing is re-coloured for the 3D view, and
 * grey stays "no cloud-free pass".
 */
export default function Lake3DOverlay({
  manifest,
  body,
  frame,
  frames,
  index,
  playing,
  speed,
  preload,
  onIndex,
  onToggle,
  onSpeed,
  onClose,
}: Lake3DOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<LakeSceneHandle | null>(null);
  const cacheRef = useRef(new Map<string, SurfaceData>());
  const lastBodyRef = useRef<string | null>(null);
  const downRef = useRef<{ x: number; y: number } | null>(null);
  const exaggerationRef = useRef<number>(1);

  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [data, setData] = useState<SurfaceData | null>(null);
  const [exaggeration, setExaggeration] = useState<number>(1);
  const [readout, setReadout] = useState<Readout | null>(null);

  const grid = useMemo(() => mercGridOf(manifest), [manifest]);
  const frameRisk = riskAtFrame(body, frame.id);
  const band = riskBand(frameRisk);

  exaggerationRef.current = exaggeration;

  const showSurface = useCallback((surface: SurfaceData, recenter: boolean) => {
    const scene = sceneRef.current;
    if (!scene) return;
    scene.update({
      surface: surface.surface,
      relief: surface.relief,
      extentXKm: surface.crop.extentXKm,
      extentZKm: surface.crop.extentZKm,
      heightKm: surface.heightKm,
      recenter,
    });
    setData(surface);
    setStatus("ready");
  }, []);

  // One scene for the life of the overlay.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // Focus the dialog itself: without this, focus stays on the "View this lake in 3D"
    // button behind the overlay, and the app's global key handler -- which ignores keys
    // while a button has focus -- would swallow every arrow key.
    dialogRef.current?.focus();
    const scene = createLakeScene(canvas);
    sceneRef.current = scene;
    scene.setExaggeration(exaggerationRef.current);
    const parent = canvas.parentElement;
    const resize = () => {
      if (parent) scene.setSize(parent.clientWidth, parent.clientHeight);
    };
    resize();
    const observer =
      typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    if (parent && observer) observer.observe(parent);
    return () => {
      observer?.disconnect();
      scene.dispose();
      sceneRef.current = null;
      cacheRef.current.clear();
      lastBodyRef.current = null;
    };
  }, []);

  // Load (or reuse) the relief for the body + frame on screen.
  useEffect(() => {
    if (!grid) return;
    const key = `${body.id}|${frame.id}`;
    const cached = cacheRef.current.get(key);
    const recenter = lastBodyRef.current !== body.id;
    lastBodyRef.current = body.id;
    setReadout(null);
    sceneRef.current?.setMarker(null);
    if (cached) {
      showSurface(cached, recenter);
      return;
    }

    const controller = new AbortController();
    setStatus((previous) => (previous === "ready" ? previous : "loading"));
    loadSurface(`data/${frame.tile}`, grid, body, controller.signal)
      .then((surface) => {
        if (controller.signal.aborted) return;
        const cache = cacheRef.current;
        cache.set(key, surface);
        if (cache.size > CACHE_LIMIT) {
          const oldest = cache.keys().next().value;
          if (oldest !== undefined) cache.delete(oldest);
        }
        showSurface(surface, recenter);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if ((error as { name?: string })?.name === "AbortError") return;
        console.error("ripple 3D: could not build the relief", error);
        setStatus("error");
      });
    return () => controller.abort();
  }, [body, frame, grid, showSurface]);

  useEffect(() => {
    sceneRef.current?.setExaggeration(exaggeration);
  }, [exaggeration]);

  const pickAt = useCallback(
    (clientX: number, clientY: number) => {
      const canvas = canvasRef.current;
      const scene = sceneRef.current;
      if (!canvas || !scene || !data || !grid) return;
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      const ndcX = ((clientX - rect.left) / rect.width) * 2 - 1;
      const ndcY = -(((clientY - rect.top) / rect.height) * 2 - 1);
      const hit: LakePick | null = scene.pick(ndcX, ndcY);
      if (!hit) {
        scene.setMarker(null);
        setReadout(null);
        return;
      }
      scene.setMarker(hit);
      const pixel = {
        x:
          data.crop.pixels.x +
          ((hit.col + 0.5) * data.crop.pixels.width) / data.relief.width,
        y:
          data.crop.pixels.y +
          ((hit.row + 0.5) * data.crop.pixels.height) / data.relief.height,
      };
      const [lon, lat] = pixelToLonLat(pixel, grid);
      setReadout({ risk: hit.valid ? hit.risk : null, lon, lat });
    },
    [data, grid],
  );

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      className="absolute inset-0 z-40 overflow-hidden bg-slate-100 outline-none"
      data-lake3d
      data-ready={status === "ready" ? "true" : "false"}
      role="dialog"
      aria-label={`3D view of ${body.name}`}
    >
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(120% 90% at 50% 8%, #e8eef6 0%, #dde5ef 45%, #cdd8e6 100%)",
        }}
      />
      <canvas
        ref={canvasRef}
        className="absolute inset-0 h-full w-full cursor-grab touch-none active:cursor-grabbing"
        aria-label={`3D relief of ${body.name}: height and colour show the relative risk index`}
        onPointerDown={(event) => {
          downRef.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerUp={(event) => {
          const down = downRef.current;
          downRef.current = null;
          if (!down) return;
          if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > 6) return;
          pickAt(event.clientX, event.clientY);
        }}
      />

      {/* Header: what is on screen and how to leave. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between gap-3 p-3">
        <div className="pointer-events-auto max-w-[min(420px,70vw)] rounded-xl bg-white/92 px-3 py-2 shadow-sm backdrop-blur">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-sm font-semibold text-slate-900">{body.name}</h2>
            {band && (
              <span
                className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                style={{ background: `${band.color}1a`, color: band.color }}
              >
                {band.label} · {formatRisk(frameRisk)}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[10px] leading-snug text-slate-500">
            {body.area_km2 != null
              ? `${Math.round(body.area_km2).toLocaleString("en-US")} km² · `
              : ""}
            {frame.kind === "forecast"
              ? `forecast ${frame.label}`
              : frame.latest
                ? "now · latest observed composite"
                : `observed ${frame.window_label}`}
          </p>
          <p className="mt-1 text-[10px] leading-snug text-slate-400">
            Height = the same relative risk index the map colours, 3×3-smoothed and
            exaggerated — colour and height always agree. Grey and flat means no cloud-free
            pass, not clean water.
          </p>
        </div>

        <div className="pointer-events-auto flex items-center gap-1.5">
          <div
            className="flex items-center gap-1 rounded-xl bg-white/92 p-1 shadow-sm backdrop-blur"
            role="group"
            aria-label="Vertical exaggeration"
          >
            {EXAGGERATIONS.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setExaggeration(option)}
                aria-pressed={exaggeration === option}
                aria-label={`Vertical exaggeration ${option}x`}
                className={`h-11 w-11 rounded-lg text-[11px] font-semibold transition ${
                  exaggeration === option
                    ? "bg-slate-900 text-white"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                ×{option}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close 3D view"
            className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/92 text-slate-600 shadow-sm backdrop-blur transition hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      </div>

      {/* Status: only one of these is ever visible; the readout doubles as the click result. */}
      <div className="pointer-events-none absolute inset-x-0 top-1/2 z-10 flex -translate-y-1/2 justify-center">
        {status === "error" && (
          <div className="pointer-events-auto rounded-xl bg-white/95 px-4 py-3 text-center shadow-lg">
            <p className="text-[12px] font-semibold text-slate-800">
              Could not build the relief for this frame
            </p>
            <p className="mt-1 text-[10px] text-slate-500">
              The tile failed to load or decode. Try another frame, or close and reopen the
              view.
            </p>
          </div>
        )}
        {status === "ready" && data && data.surface.quadCount === 0 && (
          <div className="rounded-xl bg-white/92 px-3 py-2 text-center text-[11px] font-medium text-slate-600 shadow-sm backdrop-blur">
            No usable observation in this frame — grey means cloud, not clean. Try another
            frame.
          </div>
        )}
      </div>

      {/* Bottom strip: readout, legend, stats, and the same timeline as the map. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 p-3">
        <div className="pointer-events-auto mx-auto flex max-w-3xl flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2 rounded-xl bg-white/92 px-3 py-2 shadow-sm backdrop-blur">
            <span
              role="status"
              aria-live="polite"
              className="min-h-6 flex-1 truncate text-[11px] font-medium text-slate-700"
            >
              {readout
                ? readout.risk == null
                  ? `No water observation at ${readout.lat.toFixed(2)}°, ${readout.lon.toFixed(2)}°`
                  : `Risk ${formatRisk(readout.risk)} · ${riskBand(readout.risk)?.label ?? ""} · ${readout.lat.toFixed(2)}°, ${readout.lon.toFixed(2)}°`
                : "Click the surface to read a spot · drag to orbit · wheel to zoom"}
            </span>
            {data && status === "ready" && (
              <span className="text-[10px] text-slate-500">
                peak {formatRisk(data.surface.peak?.risk ?? null)} · mean in view{" "}
                {formatRisk(data.surface.meanRisk)} ·{" "}
                {formatPercent(data.surface.waterFraction)} water in view
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 rounded-xl bg-white/92 px-3 py-1.5 shadow-sm backdrop-blur">
            <span className="text-[10px] font-semibold text-emerald-700">cleaner</span>
            <span
              className="h-1.5 flex-1 rounded-full"
              style={{ background: RISK_GRADIENT }}
              aria-hidden="true"
            />
            <span className="text-[10px] font-semibold text-red-700">more polluted</span>
          </div>

          <Timeline
            frames={frames}
            index={index}
            playing={playing}
            speed={speed}
            onIndex={onIndex}
            onToggle={onToggle}
            onSpeed={onSpeed}
            preload={preload}
          />
        </div>
      </div>
    </div>
  );
}
