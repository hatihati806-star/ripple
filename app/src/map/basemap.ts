/**
 * Basemap: OpenFreeMap "positron" — light grey with city labels, matching the reference
 * radar map's look.
 *
 * Deliberately NOT CARTO's cartocdn endpoint: as of 2026 it watermarks keyless usage with
 * "API KEY REQUIRED" across the map. OpenFreeMap serves the same Positron style with no
 * API key and no usage limits (verified: style 200 with 55 layers, vector tiles 200).
 *
 * Ripple's whole data stack is key-free by design; the basemap must not break that.
 */
export const BASEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/positron";

export const BASEMAP_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · ' +
  '<a href="https://openfreemap.org">OpenFreeMap</a>';

export const DEFAULT_REGION_BOUNDS: [number, number, number, number] = [
  -106.0, 40.0, -82.0, 54.0,
];

/**
 * Where the MapLibre worker is served. Emitted by the `ripple:maplibre-worker` Vite plugin
 * (see vite.config.ts) because MapLibre builds this URL at runtime and bundlers cannot
 * detect it. Kept as a stable path so dev and production behave identically.
 */
export const MAPLIBRE_WORKER_URL = "/maplibre/maplibre-gl-worker.mjs";
