import type { BodyForecast, BodySample, Frame, Manifest, WaterBody } from "../domain/types";

export const DEFAULT_DISCLAIMER =
  "Satellite-retrieved optical proxies, not a regulatory measurement. " +
  "Not for drinking or swimming decisions. Bloom intensity only — " +
  "no cyanobacteria or toxicity determination.";

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asRange(value: unknown, fallback: [number, number]): [number, number] {
  if (!Array.isArray(value) || value.length !== 2) return fallback;
  const [a, b] = value;
  if (typeof a !== "number" || typeof b !== "number") return fallback;
  return [a, b];
}

function asBounds(
  value: unknown,
): [number, number, number, number] | undefined {
  if (!Array.isArray(value) || value.length !== 4) return undefined;
  return value.every((entry) => typeof entry === "number")
    ? (value as [number, number, number, number])
    : undefined;
}

function normalizeFrame(raw: unknown, index: number): Frame | null {
  if (!raw || typeof raw !== "object") return null;
  const f = raw as Partial<Frame>;
  if (typeof f.tile !== "string" || typeof f.id !== "string") return null;
  const kind = f.kind === "forecast" ? "forecast" : "observed";
  const fallbackLabel =
    kind === "forecast" ? `+${f.forecast_day ?? index}d` : (f.date ?? f.id);
  return {
    id: f.id,
    kind,
    label: typeof f.label === "string" ? f.label : String(fallbackLabel),
    window_label:
      typeof f.window_label === "string" ? f.window_label : (f.date ?? ""),
    date: typeof f.date === "string" ? f.date : "",
    tile: f.tile,
    probe_tile: typeof f.probe_tile === "string" ? f.probe_tile : f.tile,
    latest: f.latest === true,
    forecast_day: f.forecast_day,
    age_days: typeof f.age_days === "number" ? f.age_days : null,
    source_scene: f.source_scene ?? null,
    scene_count: typeof f.scene_count === "number" ? f.scene_count : undefined,
    scene_dates: Array.isArray(f.scene_dates) ? (f.scene_dates as string[]) : undefined,
    ndti_baseline: f.ndti_baseline ? asRange(f.ndti_baseline, [0, 1]) : undefined,
    ndci_baseline: f.ndci_baseline ? asRange(f.ndci_baseline, [0, 1]) : undefined,
    bloom_fraction:
      typeof f.bloom_fraction === "number" ? f.bloom_fraction : undefined,
    anomaly_mean: typeof f.anomaly_mean === "number" ? f.anomaly_mean : null,
    coverage: f.coverage,
  };
}

function normalizeSample(raw: unknown): BodySample | null {
  if (!raw || typeof raw !== "object") return null;
  const s = raw as Partial<BodySample>;
  if (typeof s.frame !== "string") return null;
  return {
    frame: s.frame,
    risk: typeof s.risk === "number" ? s.risk : null,
    risk_peak: typeof s.risk_peak === "number" ? s.risk_peak : null,
    risk_std: typeof s.risk_std === "number" ? s.risk_std : null,
    cells: typeof s.cells === "number" ? s.cells : null,
    cell_fraction: typeof s.cell_fraction === "number" ? s.cell_fraction : null,
    ndci: typeof s.ndci === "number" ? s.ndci : null,
    ndti: typeof s.ndti === "number" ? s.ndti : null,
    valid: s.valid === true,
    offset_cells: typeof s.offset_cells === "number" ? s.offset_cells : null,
    water_fraction:
      typeof s.water_fraction === "number" ? s.water_fraction : null,
  };
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is number => typeof entry === "number")
    : [];
}

function normalizeForecast(raw: unknown): BodyForecast | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const f = raw as Partial<BodyForecast>;
  if (typeof f.curve_number !== "number" || !Array.isArray(f.rain_mm)) return undefined;
  return {
    baseline_risk: asNumber(f.baseline_risk, 0),
    antecedent_rain_mm: asNumber(f.antecedent_rain_mm, 0),
    curve_number: f.curve_number,
    land_cover: typeof f.land_cover === "string" ? f.land_cover : "row_crop",
    soil_group: typeof f.soil_group === "string" ? f.soil_group : "B",
    rain_mm: numberArray(f.rain_mm),
    runoff_mm: numberArray(f.runoff_mm),
    risk: numberArray(f.risk),
  };
}

function normalizeBody(raw: unknown): WaterBody | null {
  if (!raw || typeof raw !== "object") return null;
  const b = raw as Partial<WaterBody>;
  if (typeof b.id !== "string" || typeof b.name !== "string") return null;
  if (typeof b.lat !== "number" || typeof b.lon !== "number") return null;
  const series = Array.isArray(b.series)
    ? (b.series.map(normalizeSample).filter(Boolean) as BodySample[])
    : [];
  return {
    id: b.id,
    name: b.name,
    kind: typeof b.kind === "string" ? b.kind : "lake",
    lat: b.lat,
    lon: b.lon,
    area_km2: typeof b.area_km2 === "number" ? b.area_km2 : null,
    curated: b.curated === true,
    caution: typeof b.caution === "string" ? b.caution : null,
    series,
    rain_mm: numberArray(b.rain_mm),
    forecast: normalizeForecast(b.forecast),
    latest_risk: typeof b.latest_risk === "number" ? b.latest_risk : null,
    latest_peak: typeof b.latest_peak === "number" ? b.latest_peak : null,
    latest_std: typeof b.latest_std === "number" ? b.latest_std : null,
    latest_cells: typeof b.latest_cells === "number" ? b.latest_cells : null,
    latest_cell_fraction:
      typeof b.latest_cell_fraction === "number" ? b.latest_cell_fraction : null,
    latest_ndci: typeof b.latest_ndci === "number" ? b.latest_ndci : null,
    latest_ndti: typeof b.latest_ndti === "number" ? b.latest_ndti : null,
    observed_frames: asNumber(b.observed_frames, 0),
    total_frames: asNumber(b.total_frames, 0),
  };
}

/** Validate an untrusted manifest payload, or return null if unusable. */
export function normalizeManifest(raw: unknown): Manifest | null {
  if (!raw || typeof raw !== "object") return null;
  const m = raw as Partial<Manifest>;
  if (!Array.isArray(m.frames) || !m.region_bbox || !m.scales) return null;

  const frames = m.frames
    .map((frame, index) => normalizeFrame(frame, index))
    .filter((frame): frame is Frame => frame !== null);
  if (frames.length === 0) return null;

  // v1 manifests carried no `latest` flag: the newest observed frame is it.
  let sawLatest = false;
  for (let index = frames.length - 1; index >= 0; index--) {
    if (frames[index].kind === "observed" && !sawLatest) {
      frames[index] = { ...frames[index], latest: true };
      sawLatest = true;
    }
  }

  const grid = m.grid
    ? {
        width: asNumber(m.grid.width, 0),
        height: asNumber(m.grid.height, 0),
        crs: m.grid.crs ?? "EPSG:3857",
        merc_bounds: asBounds(m.grid.merc_bounds),
      }
    : undefined;

  return {
    schema_version: m.schema_version,
    generated_utc: m.generated_utc ?? "",
    region_name: m.region_name,
    region_bbox: m.region_bbox,
    discovery: m.discovery,
    resolution_m: asNumber(m.resolution_m, 250),
    requested_resolution_m: m.requested_resolution_m,
    grid: grid && grid.width > 0 && grid.height > 0 ? grid : undefined,
    probe: m.probe,
    palette: m.palette ?? [],
    scales: m.scales,
    bloom_threshold_ndci: m.bloom_threshold_ndci,
    ramp_semantics: m.ramp_semantics,
    body_statistic: m.body_statistic,
    body_stats_method: m.body_stats_method,
    body_stats_recomputed_utc: m.body_stats_recomputed_utc,
    normalization: m.normalization,
    forecast: m.forecast ?? null,
    coverage: m.coverage,
    water_bodies: Array.isArray(m.water_bodies)
      ? (m.water_bodies.map(normalizeBody).filter(Boolean) as WaterBody[])
      : [],
    frames,
    disclaimer: m.disclaimer ?? DEFAULT_DISCLAIMER,
  };
}

export async function loadManifest(baseUrl = "data"): Promise<Manifest | null> {
  try {
    const res = await fetch(`${baseUrl}/manifest.json`, { cache: "no-cache" });
    if (!res.ok) return null;
    return normalizeManifest(await res.json());
  } catch {
    return null;
  }
}
