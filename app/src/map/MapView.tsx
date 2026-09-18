import { useEffect, useRef, useState } from "react";
import {
  Map,
  NavigationControl,
  setWorkerUrl,
  type GeoJSONSource,
  type ImageSource,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { BASEMAP_ATTRIBUTION, BASEMAP_STYLE_URL, MAPLIBRE_WORKER_URL } from "./basemap";
import {
  MAP3D_BEARING,
  MAP3D_PITCH,
  TERRAIN_ATTRIBUTION,
  TERRAIN_ENCODING,
  TERRAIN_SOURCE_ID,
  TERRAIN_TILE_TEMPLATE,
  terrainSpec,
} from "./terrain";
import type { Selection } from "../domain/types";
import { riskColor } from "../domain/palette";

// Must run before the first Map is constructed. See vite.config.ts for why MapLibre's
// self-resolved worker URL is unusable under a bundler.
setWorkerUrl(MAPLIBRE_WORKER_URL);

const RASTER_A = "ripple-a";
const RASTER_B = "ripple-b";
const BODIES_SOURCE = "ripple-bodies";
const BODY_DOT = "ripple-body-dot";
const BODY_RING = "ripple-body-ring";
const PICK_SOURCE = "ripple-pick";
const FADE_MS = 260;

export interface BodyMarker {
  id: string;
  name: string;
  lon: number;
  lat: number;
  risk: number | null;
}

export interface MapApi {
  focus(lon: number, lat: number, zoom?: number): void;
}

interface MapViewProps {
  /** Tile for the frame being shown. */
  tileUrl: string | null;
  overlayBounds: [number, number, number, number];
  fitBounds?: [number, number, number, number];
  markers: BodyMarker[];
  selection: Selection;
  onSelectBody: (id: string) => void;
  onPickPoint: (lon: number, lat: number) => void;
  onReady?: (api: MapApi) => void;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );
}

function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3;
}

/** Resolve once MapLibre has the new image for `sourceId`, or after `timeoutMs`. */
function waitForSource(map: Map, sourceId: string, timeoutMs = 500): Promise<void> {
  return new Promise((resolve) => {
    if (map.isSourceLoaded(sourceId)) {
      resolve();
      return;
    }
    const timer = window.setTimeout(finish, timeoutMs);
    function finish() {
      window.clearTimeout(timer);
      map.off("sourcedata", onData);
      resolve();
    }
    function onData(event: { sourceId?: string; isSourceLoaded?: boolean }) {
      if (event.sourceId === sourceId && map.isSourceLoaded(sourceId)) finish();
    }
    map.on("sourcedata", onData);
  });
}

function toFeatureCollection(markers: BodyMarker[], selection: Selection) {
  const selectedId = selection?.kind === "body" ? selection.id : null;
  return {
    type: "FeatureCollection" as const,
    features: markers.map((marker) => ({
      type: "Feature" as const,
      id: marker.id,
      properties: {
        id: marker.id,
        name: marker.name,
        risk: marker.risk,
        color: riskColor(marker.risk ?? 0),
        valid: marker.risk != null,
        selected: marker.id === selectedId,
      },
      geometry: { type: "Point" as const, coordinates: [marker.lon, marker.lat] },
    })),
  };
}

/**
 * MapLibre wrapper.
 *
 * Frames are swapped through two raster layers and crossfaded with a 260 ms tween, after
 * the incoming image has actually been decoded. Swapping one source in place would blank
 * the layer for however long the image takes to load -- the difference between a radar
 * loop and a slideshow of holes. The pipeline builds its grid in Web Mercator, but an
 * `image` source takes lon/lat corner coordinates and MapLibre reprojects internally, so
 * the overlay lands correctly either way.
 */
export function MapView({
  tileUrl,
  overlayBounds,
  fitBounds,
  markers,
  selection,
  onSelectBody,
  onPickPoint,
  onReady,
}: MapViewProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const [ready, setReady] = useState(false);
  const [mode3d, setMode3d] = useState(false);
  const [hovered, setHovered] = useState<{ name: string; risk: number | null; x: number; y: number } | null>(null);
  const frontRef = useRef<"a" | "b">("a");
  const tweenRef = useRef<number | null>(null);
  const selectionRef = useRef<Selection>(selection);
  const onPickRef = useRef(onPickPoint);
  const onSelectRef = useRef(onSelectBody);
  const tileRef = useRef<string | null>(tileUrl);
  /** URL currently shown by the front layer, so a no-op frame change costs nothing. */
  const displayedUrlRef = useRef<string | null>(null);

  selectionRef.current = selection;
  onPickRef.current = onPickPoint;
  onSelectRef.current = onSelectBody;
  tileRef.current = tileUrl;

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new Map({
      container: container.current,
      style: BASEMAP_STYLE_URL,
      bounds: fitBounds ?? [-106.0, 40.0, -82.0, 54.0],
      fitBoundsOptions: { padding: 32 },
      attributionControl: { compact: true },
    });
    map.addControl(new NavigationControl({ showCompass: true }), "top-right");

    map.on("load", () => {
      if (!map.getSource("ripple-attribution")) {
        map.addSource("ripple-attribution", {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
          attribution: BASEMAP_ATTRIBUTION,
        });
      }

      const coordinates: [
        [number, number],
        [number, number],
        [number, number],
        [number, number],
      ] = [
        [overlayBounds[0], overlayBounds[3]],
        [overlayBounds[2], overlayBounds[3]],
        [overlayBounds[2], overlayBounds[1]],
        [overlayBounds[0], overlayBounds[1]],
      ];
      const initial = tileRef.current;
      for (const [id, visible] of [
        [RASTER_A, true],
        [RASTER_B, false],
      ] as const) {
        if (visible && initial) {
          map.addSource(id, { type: "image", url: initial, coordinates });
        } else {
          // A 1x1 transparent placeholder: an image source needs a URL up front, and
          // updateImage() only works on an existing source.
          map.addSource(id, { type: "image", url: TRANSPARENT_PIXEL, coordinates });
        }
        map.addLayer({
          id,
          type: "raster",
          source: id,
          paint: {
            "raster-opacity": visible && initial ? 0.9 : 0,
            "raster-fade-duration": 0,
          },
        });
      }

      map.addSource(BODIES_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: BODY_DOT,
        type: "circle",
        source: BODIES_SOURCE,
        filter: ["==", ["get", "valid"], true],
        paint: {
          "circle-radius": ["case", ["get", "selected"], 8, 5.5],
          "circle-color": ["get", "color"],
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.95,
        },
      });
      map.addLayer({
        id: BODY_RING,
        type: "circle",
        source: BODIES_SOURCE,
        filter: ["==", ["get", "selected"], true],
        paint: {
          "circle-radius": 14,
          "circle-color": "transparent",
          "circle-stroke-width": 2.5,
          "circle-stroke-color": ["get", "color"],
          "circle-stroke-opacity": 0.9,
        },
      });

      map.addSource(PICK_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "ripple-pick-pulse",
        type: "circle",
        source: PICK_SOURCE,
        paint: {
          "circle-radius": 10,
          "circle-color": "transparent",
          "circle-stroke-width": 2,
          "circle-stroke-color": "#0F172A",
          "circle-stroke-opacity": 0.7,
        },
      });
      map.addLayer({
        id: "ripple-pick-dot",
        type: "circle",
        source: PICK_SOURCE,
        paint: {
          "circle-radius": 4,
          "circle-color": "#0F172A",
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
        },
      });

      map.on("click", BODY_DOT, (event) => {
        const feature = event.features?.[0];
        const id = feature?.properties?.id;
        if (typeof id === "string") onSelectRef.current(id);
      });
      map.on("click", (event) => {
        const hits = map.queryRenderedFeatures(event.point, { layers: [BODY_DOT] });
        if (hits.length > 0) return;
        onPickRef.current(event.lngLat.lng, event.lngLat.lat);
      });
      map.on("mouseenter", BODY_DOT, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", BODY_DOT, () => {
        map.getCanvas().style.cursor = "";
        setHovered(null);
      });
      map.on("mousemove", BODY_DOT, (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        setHovered({
          name: String(feature.properties?.name ?? ""),
          risk: typeof feature.properties?.risk === "number" ? feature.properties.risk : null,
          x: event.point.x,
          y: event.point.y,
        });
      });

      setReady(true);
      displayedUrlRef.current = tileRef.current;
    });

    mapRef.current = map;
    if (import.meta.env.DEV) {
      // Debug handle for development only; not exposed in production builds.
      (window as unknown as { __rippleMap?: Map }).__rippleMap = map;
    }
    return () => {
      if (tweenRef.current !== null) window.cancelAnimationFrame(tweenRef.current);
      map.remove();
      mapRef.current = null;
      setReady(false);
    };
    // The map is created once; overlay bounds are fixed for the life of the app.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (map && ready && onReady) onReady({ focus: (lon, lat, zoom = 6.4) => focusMap(map, lon, lat, zoom) });
  }, [ready, onReady]);

  // 3D mode: tilt the camera and drape everything over real terrain. The pollution overlay
  // is an ordinary raster layer, so it follows the terrain exactly like the basemap does --
  // the lakes sit in their real basins with the risk colours still on them.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (mode3d) {
      if (!map.getSource(TERRAIN_SOURCE_ID)) {
        map.addSource(TERRAIN_SOURCE_ID, {
          type: "raster-dem",
          tiles: [TERRAIN_TILE_TEMPLATE],
          encoding: TERRAIN_ENCODING,
          tileSize: 256,
          attribution: TERRAIN_ATTRIBUTION,
        });
      }
      map.setTerrain(terrainSpec());
      map.easeTo({
        pitch: MAP3D_PITCH,
        bearing: MAP3D_BEARING,
        duration: prefersReducedMotion() ? 0 : 900,
        essential: true,
      });
    } else {
      map.setTerrain(null);
      map.easeTo({
        pitch: 0,
        bearing: 0,
        duration: prefersReducedMotion() ? 0 : 700,
        essential: true,
      });
    }
  }, [mode3d, ready]);

  // Crossfade between frames.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !tileUrl) return;
    if (displayedUrlRef.current === tileUrl) return;
    const incomingId = frontRef.current === "a" ? RASTER_B : RASTER_A;
    const outgoingId = frontRef.current === "a" ? RASTER_A : RASTER_B;
    const incoming = map.getSource(incomingId) as ImageSource | undefined;
    if (!incoming) return;

    let cancelled = false;
    if (tweenRef.current !== null) {
      window.cancelAnimationFrame(tweenRef.current);
      tweenRef.current = null;
    }
    map.setPaintProperty(outgoingId, "raster-opacity", 0.9);

    incoming.updateImage({ url: tileUrl });
    if (map.getLayer(BODY_DOT)) map.moveLayer(incomingId, BODY_DOT);

      void waitForSource(map, incomingId).then(() => {
      if (cancelled || mapRef.current !== map) return;
      displayedUrlRef.current = tileUrl;
      if (prefersReducedMotion()) {
        map.setPaintProperty(incomingId, "raster-opacity", 0.9);
        map.setPaintProperty(outgoingId, "raster-opacity", 0);
        frontRef.current = incomingId === RASTER_A ? "a" : "b";
        return;
      }
      const started = performance.now();
      const step = (now: number) => {
        if (cancelled || mapRef.current !== map) return;
        // rAF hands back the *frame start* timestamp, which can predate `started`, so the
        // raw delta can be negative -- and easeOutCubic diverges below zero, which MapLibre
        // rejects as an out-of-range opacity. Clamp before easing.
        const t = Math.min(1, Math.max(0, (now - started) / FADE_MS));
        map.setPaintProperty(incomingId, "raster-opacity", 0.9 * easeOutCubic(t));
        if (t < 1) {
          tweenRef.current = window.requestAnimationFrame(step);
        } else {
          map.setPaintProperty(outgoingId, "raster-opacity", 0);
          frontRef.current = incomingId === RASTER_A ? "a" : "b";
          tweenRef.current = null;
        }
      };
      tweenRef.current = window.requestAnimationFrame(step);
    });

    return () => {
      cancelled = true;
    };
  }, [tileUrl, ready]);

  // Marker data follows the displayed frame and the selection.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource(BODIES_SOURCE) as GeoJSONSource | undefined;
    source?.setData(toFeatureCollection(markers, selection));
  }, [markers, selection, ready]);

  // Selected point reticle, with a pulse that respects reduced motion.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource(PICK_SOURCE) as GeoJSONSource | undefined;
    if (!source) return;
    if (selection?.kind !== "point") {
      source.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    source.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: { type: "Point", coordinates: [selection.lon, selection.lat] },
        },
      ],
    });

    if (prefersReducedMotion()) return;
    let frame = 0;
    const started = performance.now();
    const animate = (now: number) => {
      if (mapRef.current !== map) return;
      // Same rAF timestamp caveat as the crossfade: keep the phase inside [0, 1).
      const elapsed = Math.max(0, now - started);
      const phase = (elapsed % 1800) / 1800;
      map.setPaintProperty("ripple-pick-pulse", "circle-radius", 8 + phase * 16);
      map.setPaintProperty(
        "ripple-pick-pulse",
        "circle-stroke-opacity",
        0.7 * (1 - phase),
      );
      frame = window.requestAnimationFrame(animate);
    };
    frame = window.requestAnimationFrame(animate);
    return () => window.cancelAnimationFrame(frame);
  }, [selection, ready]);

  // Selection label: an HTML chip projected onto the map, rather than a symbol layer.
  // Symbol labels need glyph sets from the basemap vendor, and the Positron glyph endpoint
  // 404s for the font stack MapLibre defaults to -- a silent fallback that logs a warning
  // per character. Projecting a div costs a reprojection per frame and no network at all.
  const [selectedLabel, setSelectedLabel] = useState<{
    name: string;
    x: number;
    y: number;
  } | null>(null);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const selected =
      selection?.kind === "body"
        ? markers.find((marker) => marker.id === selection.id)
        : undefined;
    if (!selected) {
      setSelectedLabel(null);
      return;
    }
    const update = () => {
      const point = map.project([selected.lon, selected.lat]);
      setSelectedLabel({ name: selected.name, x: point.x, y: point.y });
    };
    update();
    map.on("move", update);
    map.on("zoom", update);
    return () => {
      map.off("move", update);
      map.off("zoom", update);
    };
  }, [selection, markers, ready]);

  // The positioning wrapper is deliberately separate from the MapLibre container:
  // MapLibre's own stylesheet sets `.maplibregl-map { position: relative }`, which
  // overrides Tailwind's `absolute` at equal specificity. Applying `absolute inset-0`
  // directly to the map element therefore collapses it to zero height.
  return (
    <div className="absolute inset-0">
      <div ref={container} className="h-full w-full" />
      {/* Sits beside MapLibre's own control group, with the same 44 px size. Below the
          group would collide with the compass, whose control stack grows with it. */}
      <button
        type="button"
        onClick={() => setMode3d((enabled) => !enabled)}
        aria-pressed={mode3d}
        aria-label="Toggle 3D terrain view"
        className={`absolute right-[62px] top-3 z-10 flex h-11 w-11 items-center justify-center rounded-lg text-[11px] font-bold shadow-sm backdrop-blur transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 ${
          mode3d
            ? "bg-slate-900 text-white"
            : "bg-white/92 text-slate-700 hover:text-slate-900"
        }`}
      >
        3D
      </button>
      {selectedLabel && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-[calc(100%+16px)] whitespace-nowrap rounded-full bg-slate-900/92 px-2 py-0.5 text-[11px] font-semibold text-white shadow-lg"
          style={{ left: selectedLabel.x, top: selectedLabel.y }}
        >
          {selectedLabel.name}
        </div>
      )}
      {hovered && (
        <div
          className="pointer-events-none absolute z-20 -translate-x-1/2 -translate-y-[calc(100%+10px)] rounded-lg bg-slate-900/92 px-2 py-1 text-[11px] font-medium text-white shadow-lg"
          style={{ left: hovered.x, top: hovered.y }}
        >
          {hovered.name}
          <span className="ml-1.5 text-white/60">
            {hovered.risk == null ? "no data" : `${Math.round(hovered.risk * 100)}`}
          </span>
        </div>
      )}
    </div>
  );
}

const TRANSPARENT_PIXEL =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

function focusMap(map: Map, lon: number, lat: number, zoom: number) {
  map.flyTo({
    center: [lon, lat],
    zoom: Math.max(map.getZoom(), zoom),
    duration: prefersReducedMotion() ? 0 : 1500,
    essential: true,
    curve: 1.35,
  });
}
