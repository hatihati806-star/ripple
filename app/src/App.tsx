import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MapApi, BodyMarker } from "./map/MapView";
import { Legend } from "./legend/Legend";
import { Timeline } from "./timeline/Timeline";
import { DetailPanel } from "./detail/DetailPanel";
import { Leaderboard } from "./leaderboard/Leaderboard";
import { AssistantPanel } from "./assistant/AssistantPanel";
import type { AssistantAction } from "./assistant/actions";
import { useTimeline } from "./timeline/useTimeline";
import { useMediaQuery } from "./ui/useMediaQuery";
import { loadManifest } from "./data/manifest";
import { preloadFrames, preloadImage, type PreloadProgress } from "./data/frames";
import {
  loadProbeCanvases,
  sampleProbes,
  type ProbeCanvas,
  type ProbeResult,
} from "./inspect/probe";
import { lonLatToMercator } from "./map/projection";
import { riskAtFrame } from "./water/ranking";
import type { Manifest, Selection } from "./domain/types";

type ProbeStatus = "idle" | "loading" | "ready" | "error";

// MapLibre is ~1.2 MB of the bundle and is useless until the map container exists, so it
// loads as its own chunk: the shell, legend and timeline paint while it streams in.
const MapView = lazy(() =>
  import("./map/MapView").then((module) => ({ default: module.MapView })),
);

// three.js is only worth its weight once someone opens a lake in 3D, so the whole scene
// arrives as a second lazy chunk on first use.
const Lake3DOverlay = lazy(() => import("./lake3d/Lake3DOverlay"));

function MapSkeleton() {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-slate-100">
      <div className="flex items-center gap-2 rounded-full bg-white/92 px-3 py-2 text-[11px] font-medium text-slate-500 shadow-sm">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
        Loading map…
      </div>
    </div>
  );
}

function mercBoundsOf(manifest: Manifest): [number, number, number, number] {
  if (manifest.grid?.merc_bounds) return manifest.grid.merc_bounds;
  const [west, south, east, north] = manifest.region_bbox;
  const [x0, y0] = lonLatToMercator(west, south);
  const [x1, y1] = lonLatToMercator(east, north);
  return [x0, y0, x1, y1];
}

export default function App() {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [selection, setSelection] = useState<Selection>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [probeStatus, setProbeStatus] = useState<ProbeStatus>("idle");
  const [preload, setPreload] = useState<PreloadProgress>({ loaded: 0, total: 0 });
  const [legendOpen, setLegendOpen] = useState(false);
  const [rankingOpen, setRankingOpen] = useState(false);
  const [lake3dOpen, setLake3dOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const mapApi = useRef<MapApi | null>(null);
  const probeCanvases = useRef<(ProbeCanvas | null)[] | null>(null);

  const narrow = useMediaQuery("(max-width: 767px)");
  const [detailsOpen, setDetailsOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadManifest().then((loaded) => {
      if (cancelled) return;
      setManifest(loaded);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const frames = useMemo(() => manifest?.frames ?? [], [manifest]);
  const timeline = useTimeline(frames);
  const bodies = useMemo(() => manifest?.water_bodies ?? [], [manifest]);

  // Default panel visibility follows the viewport: both open on desktop, both folded on
  // phones where they would cover the map.
  useEffect(() => {
    if (narrow) {
      setLegendOpen(false);
      setRankingOpen(false);
      setDetailsOpen(false);
    } else {
      setLegendOpen(true);
      setRankingOpen(true);
      setDetailsOpen(true);
    }
  }, [narrow]);

  const bounds = useMemo<[number, number, number, number]>(
    () => manifest?.region_bbox ?? [-106.0, 40.0, -82.0, 54.0],
    [manifest],
  );

  const activeFrame = timeline.frame;
  const overlayUrl = useMemo(
    () => (activeFrame ? `data/${activeFrame.tile}` : null),
    [activeFrame],
  );

  const markers = useMemo<BodyMarker[]>(() => {
    if (!activeFrame) return [];
    return bodies.map((body) => ({
      id: body.id,
      name: body.name,
      lon: body.lon,
      lat: body.lat,
      risk: riskAtFrame(body, activeFrame.id),
    }));
  }, [bodies, activeFrame]);

  // Preload the whole loop once, ordered outwards from the frame on screen. A radar loop
  // is only as smooth as its coldest frame, so this runs in the background at low
  // concurrency and reports progress into the timeline.
  useEffect(() => {
    if (frames.length === 0) return;
    let cancelled = false;
    setPreload({ loaded: 0, total: frames.length });
    void preloadFrames(frames, "data", 3, (progress) => {
      if (!cancelled) setPreload(progress);
    });
    return () => {
      cancelled = true;
    };
  }, [frames]);

  // Keep the frames either side of the playhead warm.
  useEffect(() => {
    for (const offset of [-1, 0, 1]) {
      const frame = frames[timeline.index + offset];
      if (frame) void preloadImage(`data/${frame.tile}`);
    }
  }, [frames, timeline.index]);

  // Point inspection: probe rasters are decoded once, lazily, on the first click.
  useEffect(() => {
    if (!manifest || selection?.kind !== "point") {
      if (selection?.kind !== "point") {
        setProbe(null);
        setProbeStatus("idle");
      }
      return;
    }
    let cancelled = false;
    setProbeStatus("loading");
    void (async () => {
      try {
        if (!probeCanvases.current) {
          probeCanvases.current = await loadProbeCanvases(frames, 1024, undefined, "data");
        }
        if (cancelled) return;
        const canvases = probeCanvases.current;
        const sample = canvases.find((canvas) => canvas !== null) as ProbeCanvas | undefined;
        if (!sample) {
          setProbeStatus("error");
          return;
        }
        const result = sampleProbes(
          canvases,
          { width: sample.width, height: sample.height, mercBounds: mercBoundsOf(manifest) },
          selection.lon,
          selection.lat,
        );
        setProbe(result);
        setProbeStatus("ready");
      } catch {
        if (!cancelled) setProbeStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [manifest, frames, selection]);

  // Frames change with a new manifest: drop cached probe canvases.
  useEffect(() => {
    probeCanvases.current = null;
  }, [manifest]);

  const selectBody = useCallback(
    (id: string) => {
      const body = bodies.find((entry) => entry.id === id);
      setSelection({ kind: "body", id });
      setDetailsOpen(true);
      if (body) mapApi.current?.focus(body.lon, body.lat, 6.6);
    },
    [bodies],
  );

  const pickPoint = useCallback((lon: number, lat: number) => {
    setSelection({ kind: "point", lon, lat });
    setDetailsOpen(true);
  }, []);

  const closeDetails = useCallback(() => setDetailsOpen(false), []);

  // Keyboard control for the radar loop.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      // A focused button has no use for an arrow or Escape, so those two keep working
      // after a click lands on a control -- otherwise opening the 3D view (or touching
      // its controls) would silently swallow the keyboard. Space still belongs to the
      // focused button, which the overlay gate deliberately leaves alone.
      const buttonFocused = target?.tagName === "BUTTON";
      const overlayKey =
        event.key === "Escape" || (lake3dOpen && event.key.startsWith("Arrow"));
      if (buttonFocused && !overlayKey) {
        return;
      }
      // While the assistant is open, keys belong to it: a chat input needs its own space,
      // its own arrows and its own Escape, and the map must not scrub frames underneath it.
      if (assistantOpen) {
        if (event.key === "Escape") setAssistantOpen(false);
        return;
      }
      if (event.key === "ArrowRight") {
        timeline.next();
      } else if (event.key === "ArrowLeft") {
        timeline.prev();
      } else if (event.key === " ") {
        event.preventDefault();
        timeline.toggle();
      } else if (event.key === "Escape") {
        // Escape unwinds one layer at a time: the 3D view first, then the selection.
        if (lake3dOpen) {
          setLake3dOpen(false);
        } else {
          setDetailsOpen(false);
          setSelection(null);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [timeline, lake3dOpen, assistantOpen]);

  const selectedBody = useMemo(
    () =>
      selection?.kind === "body"
        ? (bodies.find((body) => body.id === selection.id) ?? null)
        : null,
    [bodies, selection],
  );

  // Losing the selection (Esc, Clear) must not leave a 3D view of a body nobody selected.
  useEffect(() => {
    if (!selectedBody) setLake3dOpen(false);
  }, [selectedBody]);

  const onMapReady = useCallback((api: MapApi) => {
    mapApi.current = api;
  }, []);

  /**
   * Run an action the assistant asked for.
   *
   * Only the app can do these: it owns the map handle, the timeline and the 3D overlay. An
   * action whose body is not in the catalogue is dropped here rather than half-performed --
   * the server already refused names it could not resolve, and this is the second gate.
   */
  const runAssistantAction = useCallback(
    async (action: AssistantAction) => {
      if (action.bodyId) {
        const body = bodies.find((entry) => entry.id === action.bodyId);
        if (!body) return;
        if (action.kind === "fly_to" || action.kind === "open_3d") {
          setSelection({ kind: "body", id: body.id });
          setDetailsOpen(true);
          mapApi.current?.focus(body.lon, body.lat, 6.6);
          if (action.kind === "open_3d") {
            // Let the fly-to land before the overlay takes over, so the two are not
            // competing for the same attention.
            await new Promise((resolve) => window.setTimeout(resolve, 900));
            setLake3dOpen(true);
          }
        }
        return;
      }
      if (action.kind === "set_frame" && action.frame) {
        const index = frames.findIndex((frame) => frame.id === action.frame);
        if (index >= 0) timeline.setIndex(index);
      }
    },
    [bodies, frames, timeline],
  );

  return (
    <div className="relative h-dvh w-full overflow-hidden bg-slate-100">
      <Suspense fallback={<MapSkeleton />}>
        <MapView
          tileUrl={overlayUrl}
          overlayBounds={bounds}
          fitBounds={bounds}
          markers={markers}
          selection={selection}
          onSelectBody={selectBody}
          onPickPoint={pickPoint}
          onReady={onMapReady}
        />
      </Suspense>

      {/* Left column: identity, legend, ranking. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between gap-3 p-3">
        {/* Narrow screens have ~350 px of usable width; the fixed right-hand control group
            and this column must fit inside it without clipping. */}
        <div className="panel-scroll pointer-events-auto flex max-h-[calc(100dvh-160px)] w-44 flex-col gap-2 overflow-y-auto sm:w-64 md:w-72">
          <div className="rounded-xl bg-white/92 px-3 py-2 shadow-sm backdrop-blur">
            <h1 className="text-sm font-semibold tracking-tight text-slate-900">Ripple</h1>
            <p className="text-[11px] leading-snug text-slate-500">
              Water quality now &amp; a 7-day runoff outlook
            </p>
            {manifest?.region_name && (
              <p className="mt-0.5 text-[10px] leading-snug text-slate-400">
                {manifest.region_name}
              </p>
            )}
          </div>
          <Legend
            manifest={manifest}
            collapsed={!legendOpen}
            onToggle={() => setLegendOpen((open) => !open)}
          />
          {!narrow && rankingOpen && (
            <Leaderboard
              bodies={bodies}
              selectedId={selection?.kind === "body" ? selection.id : null}
              discovery={manifest?.discovery}
              onSelect={selectBody}
              onClose={() => setRankingOpen(false)}
            />
          )}
        </div>

        {narrow && (
          <div className="pointer-events-auto mt-24 flex shrink-0 gap-1.5">
            <button
              type="button"
              onClick={() => setAssistantOpen(true)}
              className="min-h-11 rounded-xl bg-white/92 px-3 py-2 text-[11px] font-semibold text-slate-700 shadow-sm backdrop-blur"
            >
              ✦ Ask
            </button>
            <button
              type="button"
              onClick={() => setRankingOpen((open) => !open)}
              className="min-h-11 rounded-xl bg-white/92 px-3 py-2 text-[11px] font-semibold text-slate-700 shadow-sm backdrop-blur"
            >
              Lakes
            </button>
            {activeFrame && (
              <button
                type="button"
                onClick={() => setDetailsOpen((open) => !open)}
                className="min-h-11 rounded-xl bg-white/92 px-3 py-2 text-[11px] font-semibold text-slate-700 shadow-sm backdrop-blur"
              >
                Details
              </button>
            )}
          </div>
        )}
      </div>

      {/* Detail panel: side panel on desktop, sheet above the timeline on phones. */}
      {detailsOpen && manifest && activeFrame && (selectedBody || probe || selection?.kind === "point") && (
        <div
          className={
            narrow
              ? "pointer-events-none absolute inset-x-0 bottom-0 z-20 max-h-[62vh] overflow-y-auto p-3"
              : "panel-scroll pointer-events-none absolute right-3 top-16 z-20 max-h-[calc(100dvh-150px)] w-[340px] overflow-y-auto"
          }
        >
          <DetailPanel
            manifest={manifest}
            frames={frames}
            frameIndex={timeline.index}
            selection={selection}
            body={selectedBody}
            probe={probe}
            probeStatus={probeStatus}
            probeSampleM={manifest.probe?.sample_m ?? null}
            onClose={closeDetails}
            onOpen3D={selectedBody ? () => setLake3dOpen(true) : undefined}
          />
        </div>
      )}

      {/* Full-screen 3D analysis of the selected body; the map is inert behind it. */}
      {lake3dOpen && manifest && selectedBody && activeFrame && (
        <Suspense
          fallback={
            <div className="absolute inset-0 z-40 flex items-center justify-center bg-slate-100">
              <div className="flex items-center gap-2 rounded-full bg-white/92 px-3 py-2 text-[11px] font-medium text-slate-500 shadow-sm">
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
                Building the 3D view…
              </div>
            </div>
          }
        >
          <Lake3DOverlay
            manifest={manifest}
            body={selectedBody}
            frame={activeFrame}
            frames={frames}
            index={timeline.index}
            playing={timeline.playing}
            speed={timeline.speed}
            preload={preload}
            onIndex={timeline.setIndex}
            onToggle={timeline.toggle}
            onSpeed={timeline.setSpeed}
            onClose={() => setLake3dOpen(false)}
          />
        </Suspense>
      )}

      {/* The assistant. Wide screens get it docked to the right edge, full height above the
          timeline; phones get the same panel as a sheet. It never covers the timeline. */}
      {assistantOpen && (
        <div
          className={`pointer-events-none absolute z-30 ${
            narrow
              ? "inset-x-3 top-3 bottom-[188px]"
              : "bottom-24 right-3 top-3 w-[352px]"
          }`}
        >
          <AssistantPanel
            factsUrl="data/assistant.json"
            onAction={runAssistantAction}
            onClose={() => setAssistantOpen(false)}
          />
        </div>
      )}

      {/* Wide screens also get a launcher pill, so the assistant is reachable without
          hunting for the button in the left column. */}
      {!narrow && !assistantOpen && (
        <button
          type="button"
          onClick={() => setAssistantOpen(true)}
          className="pointer-events-auto absolute bottom-24 right-3 z-20 flex min-h-11 items-center gap-2 rounded-full bg-slate-900 px-4 text-[12px] font-semibold text-white shadow-lg transition hover:bg-slate-700"
        >
          <span aria-hidden="true" className="text-orange-400">✦</span>
          Ask Ripple
        </button>
      )}

      {/* Empty state on wide screens: tell the user what this thing does. */}
      {!narrow && detailsOpen && !selectedBody && !probe && selection?.kind !== "point" && (
        <div className="pointer-events-none absolute right-3 top-16 z-20 w-[340px]">
          <div className="animate-panel-in rounded-xl bg-white/92 p-3 shadow-lg backdrop-blur">
            <h2 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Inspect the water
            </h2>
            <ul className="mt-1.5 space-y-1.5 text-[11px] leading-snug text-slate-600">
              <li>
                <span className="font-semibold">Play the loop</span> (or press space) to watch
                the last {frames.filter((frame) => frame.kind === "observed").length}{" "}
                composites — the bloom season moving in.
              </li>
              <li>
                <span className="font-semibold">Click a coloured dot</span> for a body’s
                history, its bloom index, and the 7-day runoff outlook — then open it in
                3D to see bloom pockets as relief.
              </li>
              <li>
                <span className="font-semibold">
                  {bodies.length} water bodies
                </span>{" "}
                were found by sieving the composited mask
                {manifest?.discovery
                  ? ` (≥${Math.round(manifest.discovery.min_body_km2)} km²)`
                  : ""}
                , not chosen by hand. Click anywhere to sample that point instead.
              </li>
            </ul>
            <p className="mt-2 text-[10px] leading-snug text-slate-400">
              Grey means no cloud-free pass in that window, not clean water.
              Keyboard: ← → step frames · space plays · esc clears the selection.
            </p>
          </div>
        </div>
      )}

      {/* Timeline. On wide screens the detail panel owns the right edge, so the centred
          timeline shifts left rather than sliding underneath it. Hidden while the 3D view
          is open, which renders its own copy so the tablist stays unique. */}
      {!lake3dOpen && (
      <div
        className={`pointer-events-none absolute bottom-0 left-0 z-10 w-full p-3 transition-[padding] duration-300 ${
          !narrow && detailsOpen && (selectedBody || probe || selection?.kind === "point")
            ? "md:pr-[364px]"
            : ""
        }`}
      >
        <div
          className={`pointer-events-auto mx-auto flex max-w-3xl flex-col gap-2 ${
            narrow && detailsOpen && (selectedBody || selection?.kind === "point")
              ? "opacity-0"
              : ""
          }`}
        >
          <Timeline
            frames={frames}
            index={timeline.index}
            playing={timeline.playing}
            speed={timeline.speed}
            onIndex={timeline.setIndex}
            onToggle={timeline.toggle}
            onSpeed={timeline.setSpeed}
            preload={preload}
          />
          {selection && (
            <button
              type="button"
              onClick={() => {
                setSelection(null);
                setDetailsOpen(false);
              }}
              className="min-h-11 self-start rounded-lg bg-white/92 px-3 py-1.5 text-[11px] font-semibold text-slate-600 shadow-sm backdrop-blur"
            >
              Clear selection
            </button>
          )}
        </div>
      </div>
      )}

      {/* Narrow screens: ranking as a sheet over the map. */}
      {narrow && rankingOpen && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-30 p-3">
          <Leaderboard
            bodies={bodies}
            selectedId={selection?.kind === "body" ? selection.id : null}
            discovery={manifest?.discovery}
            onSelect={selectBody}
            onClose={() => setRankingOpen(false)}
          />
        </div>
      )}

      {!loading && frames.length === 0 && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center p-6">
          <div className="pointer-events-auto max-w-sm rounded-xl bg-white/95 p-4 text-center shadow-lg">
            <h2 className="text-sm font-semibold text-slate-800">No composite yet</h2>
            <p className="mt-1 text-[11px] leading-snug text-slate-600">
              Run the pipeline to generate frames:
            </p>
            <code className="mt-2 block rounded bg-slate-100 px-2 py-1 text-left text-[10px] text-slate-700">
              cd ripple/pipeline
              <br />
              python -m ripple_pipeline.cli --out ../app/public/data
            </code>
          </div>
        </div>
      )}
    </div>
  );
}
