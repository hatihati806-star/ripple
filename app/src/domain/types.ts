export type FrameKind = "observed" | "forecast";

export interface FrameCoverage {
  satellite_valid_fraction: number;
  water_fraction_of_grid: number;
}

export interface Frame {
  id: string;
  kind: FrameKind;
  /** Short label for the timeline chip, e.g. "Aug 21" or "+3d". */
  label: string;
  /** Longer description, e.g. "Aug 17 - Aug 27" or "Thu Sep 18". */
  window_label: string;
  /** ISO date the frame closes on. */
  date: string;
  tile: string;
  probe_tile: string;
  /** True for the newest observed composite ("now"). */
  latest: boolean;
  forecast_day?: number;
  age_days: number | null;
  source_scene?: string | null;
  scene_count?: number;
  scene_dates?: string[];
  ndti_baseline?: [number, number];
  ndci_baseline?: [number, number];
  bloom_fraction?: number;
  anomaly_mean?: number | null;
  coverage?: FrameCoverage;
}

export interface BodySample {
  frame: string;
  /** Areal mean risk over the body's own footprint: the ranked reading. */
  risk: number | null;
  /** Worst single cell in the footprint, kept beside the mean. */
  risk_peak?: number | null;
  /** Spread of risk across the footprint. */
  risk_std?: number | null;
  /** Footprint cells carrying a reading. */
  cells?: number | null;
  /** Share of the footprint that carried a reading. */
  cell_fraction?: number | null;
  ndci?: number | null;
  ndti?: number | null;
  valid: boolean;
  offset_cells?: number | null;
  /** Fraction of the sampled cell that is water; low values mean a mixed shoreline pixel. */
  water_fraction?: number | null;
}

/** The inputs and outputs of one body's SCS curve-number forecast. */
export interface BodyForecast {
  baseline_risk: number;
  antecedent_rain_mm: number;
  curve_number: number;
  land_cover: string;
  soil_group: string;
  rain_mm: number[];
  runoff_mm: number[];
  risk: number[];
}

export interface WaterBody {
  id: string;
  name: string;
  kind: string;
  lat: number;
  lon: number;
  /** Ground area measured from the composited water mask. */
  area_km2: number | null;
  /** True when a catalogued name matched this polygon; false for coordinate-named bodies. */
  curated: boolean;
  /** A property of the water that makes an optical index unreliable there. */
  caution?: string | null;
  series: BodySample[];
  /** Daily forecast rainfall at the body, aligned with `Manifest.forecast.dates`. */
  rain_mm: number[];
  forecast?: BodyForecast;
  /** Areal mean risk over the footprint on the latest observed frame. */
  latest_risk: number | null;
  /** Worst single cell in the footprint on the latest observed frame. */
  latest_peak?: number | null;
  latest_std?: number | null;
  /** Footprint cells carrying a reading on the latest observed frame. */
  latest_cells?: number | null;
  latest_cell_fraction?: number | null;
  latest_ndci: number | null;
  latest_ndti: number | null;
  observed_frames: number;
  total_frames: number;
}

export interface ForecastMeta {
  horizon_days: number;
  dates: string[];
  region_rain_mm: number[];
  basis: string;
  assumptions?: {
    land_cover?: string;
    soil_group?: string;
    anomaly_divisor?: number;
  };
}

export interface Normalization {
  basis: string;
  ndci: [number, number];
  ndti: [number, number];
  clamped?: boolean;
}

export interface Manifest {
  schema_version?: number;
  generated_utc: string;
  /** Human-readable region, e.g. "United States, southern Canada & Mexico". */
  region_name?: string;
  region_bbox: [number, number, number, number];
  /** How the catalogued bodies were found, for the UI to state its own coverage. */
  discovery?: {
    min_body_km2: number;
    max_bodies: number;
    found: number;
    named: number;
    mask_subsample?: number;
    water_threshold?: number;
  };
  resolution_m: number;
  requested_resolution_m?: number;
  grid?: {
    width: number;
    height: number;
    crs: string;
    merc_bounds?: [number, number, number, number];
  };
  probe?: { width: number; height: number; sample_m: number };
  palette: [number, number[]][];
  scales: { ndci: number[]; ndti: number[] };
  bloom_threshold_ndci?: number;
  ramp_semantics?: string;
  /** How a body's risk is reduced from its footprint, stated for the UI to repeat. */
  body_statistic?: string;
  body_stats_method?: string;
  body_stats_recomputed_utc?: string;
  normalization?: Normalization;
  forecast?: ForecastMeta | null;
  coverage?: {
    satellite_valid_fraction: number;
    water_fraction_of_grid: number;
    scenes_mosaicked: number;
  };
  water_bodies?: WaterBody[];
  frames: Frame[];
  disclaimer: string;
}

/** A selected water body, or an arbitrary inspected point. */
export type Selection =
  | { kind: "body"; id: string }
  | { kind: "point"; lon: number; lat: number; label?: string }
  | null;
