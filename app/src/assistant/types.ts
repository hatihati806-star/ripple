/** Shapes shared by the fact base, the retrieval step and the assistant endpoint. */
import type { AssistantAction } from "./actions";

export type { AssistantAction };

export interface FactFrame {
  id: string;
  kind: "observed" | "forecast";
  label?: string;
  date?: string;
  window_label?: string;
  forecast_day?: number;
  scene_count?: number;
  age_days?: number | null;
  anomaly_mean?: number | null;
  coverage?: { satellite_valid_fraction?: number; water_fraction_of_grid?: number };
}

export interface FactSample {
  frame: string;
  date?: string;
  risk: number | null;
  risk_peak?: number | null;
  cells?: number | null;
  valid: boolean;
}

export interface FactForecast {
  baseline_risk: number | null;
  antecedent_rain_mm?: number;
  curve_number?: number;
  land_cover?: string;
  soil_group?: string;
  rain_mm?: number[];
  runoff_mm?: number[];
  risk?: (number | null)[];
}

export interface FactBody {
  id: string;
  name: string;
  kind: string;
  area_km2: number | null;
  curated: boolean;
  lat: number;
  lon: number;
  caution?: string | null;
  observed: FactSample[];
  latest: FactSample | null;
  forecast: FactForecast | null;
}

export interface FactBase {
  schema: number;
  generated_utc: string;
  dataset: {
    name: string;
    one_liner: string;
    region_name?: string;
    region_bbox?: number[];
    resolution_m?: number;
    ramp_semantics?: string;
    body_statistic?: string;
    body_stats_method?: string;
    bloom_threshold_ndci?: number;
    coverage?: Record<string, number>;
    discovery?: Record<string, number>;
    frames: FactFrame[];
    body_count: number;
    bodies_with_a_reading: number;
    named_from_catalog: number;
    data_sources: string[];
    key_free: boolean;
  };
  method: {
    indices: Record<string, string>;
    forecast_basis?: string;
    forecast_dates?: string[];
    forecast_region_rain_mm?: number[];
    forecast_assumptions?: Record<string, string | number>;
    disclaimer?: string;
    limits: string[];
  };
  validation?: {
    what: string;
    window?: [string, string];
    pairs?: number;
    gauges?: number;
    turbidity_range_fnu?: [number, number];
    published_cell_value: Record<string, number | null>;
    water_only: Record<string, number | null>;
    strata: Record<string, Record<string, number | null>>;
    honest_limits: string[];
    source: string;
  };
  bodies: FactBody[];
}

export interface Source {
  label: string;
  /** What kind of record the answer leaned on, for the citation chip. */
  kind: "body" | "frame" | "method" | "validation" | "dataset";
}

export interface Retrieved {
  /** The record block handed to the model, verbatim. */
  context: string;
  sources: Source[];
  matchedBodies: string[];
  /** Which optional blocks were included, for tests and the UI. */
  blocks: string[];
}

export interface AssistantAnswer {
  answer: string;
  sources: Source[];
  /** Numbers in the answer that could not be traced to a record. */
  unverified: string[];
  /** What the assistant did: ranking it read, body it flew to, frame it stepped to. */
  actions?: AssistantAction[];
  model?: string;
  refused?: "out-of-scope" | "rate-limited" | "not-configured" | "error";
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
}
