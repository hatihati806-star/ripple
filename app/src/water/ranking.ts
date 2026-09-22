import { riskBand, type RiskBand } from "../domain/palette";
import type { Frame, WaterBody } from "../domain/types";

/** A body's peak cell minus its areal mean above which the mean is not representative. */
export const PEAK_GAP_FLAG = 0.4;
/** Below this many sampled cells a body's mean is not a body measurement. */
export const MIN_SAMPLED_CELLS = 12;
/** Below this share of the footprint sampled, the same objection applies. */
export const MIN_CELL_FRACTION = 0.25;
/** At or above this mean the whole body sits at the top of a relative ramp. */
export const SATURATED_RISK = 0.995;

export type BodyFlagKind = "peak-driven" | "thin-sample" | "caution" | "saturated";

export interface BodyFlag {
  kind: BodyFlagKind;
  label: string;
  hint: string;
  /**
   * True when the flag means the reading is not a valid reading *of this body*, so the
   * body must not outrank a well-measured one. A saturated body is extreme, not invalid:
   * it is flagged, never demoted.
   */
  demotes: boolean;
}

export interface RankedBody {
  body: WaterBody;
  /** Areal mean risk over the body's footprint. */
  risk: number;
  band: RiskBand;
  peak: number | null;
  cells: number | null;
  cellFraction: number | null;
  flags: BodyFlag[];
}

/**
 * What is wrong with this body's reading, if anything.
 *
 * Georgian Bay is the case that produced this function. Its footprint mean is 0.49 while
 * a single cell in it reads a saturated 1.00, so a leaderboard built on the worst cell
 * put a bay that is mostly clear water at the top of the list. The peak-versus-mean gap
 * is the measured symptom, not a hand-written exception list: it fires for any body whose
 * headline is set by a minority of its own water.
 */
export function assessBody(body: WaterBody): BodyFlag[] {
  const flags: BodyFlag[] = [];
  const mean = body.latest_risk;
  const peak = body.latest_peak ?? null;
  const cells = body.latest_cells ?? null;
  const fraction = body.latest_cell_fraction ?? null;

  if (body.caution) {
    flags.push({
      kind: "caution",
      label: "Index caution",
      hint: body.caution,
      demotes: true,
    });
  }

  if (mean != null && peak != null && peak - mean >= PEAK_GAP_FLAG) {
    flags.push({
      kind: "peak-driven",
      label: "Peak-driven",
      hint:
        `the worst cell in this body reads ${peak.toFixed(2)} against a body mean of ` +
        `${mean.toFixed(2)}, so its rank comes from a minority of its water`,
      demotes: true,
    });
  }

  if (cells != null && cells < MIN_SAMPLED_CELLS) {
    flags.push({
      kind: "thin-sample",
      label: "Thin sample",
      hint: `only ${cells} cells of this body carried a reading`,
      demotes: true,
    });
  } else if (fraction != null && fraction < MIN_CELL_FRACTION) {
    flags.push({
      kind: "thin-sample",
      label: "Thin sample",
      hint:
        `only ${Math.round(fraction * 100)}% of this body's footprint carried a reading`,
      demotes: true,
    });
  }

  if (mean != null && mean >= SATURATED_RISK) {
    flags.push({
      kind: "saturated",
      label: "At the top of the ramp",
      hint:
        "the whole body sits at the top of a range that is relative to this region, so " +
        "its rank within the red end is not resolved",
      demotes: false,
    });
  }

  return flags;
}

/**
 * Bodies with a usable reading, ranked by their areal mean.
 *
 * Ranking is on the mean, not the peak. The peak is reported beside it in `RankedBody`
 * so the detail panel and the row can both show it, but it does not decide the order:
 * 221 of the 250 catalogued bodies contain at least one saturated cell, which made the
 * peak a distinction without a difference.
 *
 * Bodies carrying a demoting flag are ordered after the un-flagged ones in both
 * directions. A reading that is not a reading *of that body* must not be presented as a
 * confident "dirtiest" or "cleanest" result.
 */
export function rankBodies(
  bodies: WaterBody[],
  order: "dirtiest" | "cleanest" = "dirtiest",
): RankedBody[] {
  const ranked: RankedBody[] = [];
  for (const body of bodies) {
    const risk = body.latest_risk;
    if (risk == null || !Number.isFinite(risk)) continue;
    const band = riskBand(risk);
    if (!band) continue;
    const flags = assessBody(body);
    ranked.push({
      body,
      risk,
      band,
      peak: body.latest_peak ?? null,
      cells: body.latest_cells ?? null,
      cellFraction: body.latest_cell_fraction ?? null,
      flags,
    });
  }
  const demoted = (entry: RankedBody) => (entry.flags.some((flag) => flag.demotes) ? 1 : 0);
  ranked.sort((a, b) => {
    if (demoted(a) !== demoted(b)) return demoted(a) - demoted(b);
    if (a.risk !== b.risk) return order === "dirtiest" ? b.risk - a.risk : a.risk - b.risk;
    return a.body.name.localeCompare(b.body.name);
  });
  return ranked;
}

export function countSampled(bodies: WaterBody[]): number {
  return bodies.filter((body) => body.latest_risk != null).length;
}

export function countDemoted(bodies: WaterBody[]): number {
  return bodies.filter(
    (body) => body.latest_risk != null && assessBody(body).some((flag) => flag.demotes),
  ).length;
}

/** One body's reading on one frame, so markers always match the displayed frame. */
export function riskAtFrame(body: WaterBody, frameId: string): number | null {
  const sample = body.series.find((entry) => entry.frame === frameId);
  if (!sample || !sample.valid) return null;
  return sample.risk ?? null;
}

export interface LastReading {
  frame: Frame;
  risk: number | null;
  ndci: number | null;
  ndti: number | null;
}

/**
 * The most recent *observed* frame this body has a valid reading on.
 *
 * Clouds are a coverage fact, not a product failure: in the latest window Manitoba was
 * overcast, so Lake Winnipeg has no reading there while its late-August bloom is well
 * sampled. Falling back to the last real observation (with its date attached) is more
 * honest than reporting "no data" for a body that was measured four days earlier.
 */
export function latestAvailable(body: WaterBody, frames: Frame[]): LastReading | null {
  const byFrame = new Map(body.series.map((sample) => [sample.frame, sample]));
  const observed = frames.filter((frame) => frame.kind === "observed");
  for (let index = observed.length - 1; index >= 0; index--) {
    const sample = byFrame.get(observed[index].id);
    if (sample?.valid && sample.risk != null) {
      return {
        frame: observed[index],
        risk: sample.risk,
        ndci: sample.ndci ?? null,
        ndti: sample.ndti ?? null,
      };
    }
  }
  return null;
}

/** Absolute bloom flag from the published NDCI threshold, never a toxicity claim. */
export function bloomFlag(
  ndci: number | null | undefined,
  threshold: number | undefined,
): { label: string; hint: string } | null {
  if (ndci == null || !Number.isFinite(ndci) || threshold == null) return null;
  return ndci > threshold
    ? {
        label: "Probable bloom",
        hint: `NDCI ${ndci.toFixed(2)} above the ${threshold.toFixed(2)} bloom threshold`,
      }
    : {
        label: "Below bloom threshold",
        hint: `NDCI ${ndci.toFixed(2)} under the ${threshold.toFixed(2)} threshold`,
      };
}

/** Observed history for one body, aligned with the observed frames. */
export function bodyHistory(
  body: WaterBody,
  frames: Frame[],
): { frame: Frame; risk: number | null }[] {
  const byFrame = new Map(body.series.map((sample) => [sample.frame, sample]));
  return frames
    .filter((frame) => frame.kind === "observed")
    .map((frame) => ({
      frame,
      risk: byFrame.get(frame.id)?.risk ?? null,
    }));
}

/** Forecast risk for one body, aligned with the forecast frames. */
export function bodyForecast(
  body: WaterBody,
  frames: Frame[],
): { frame: Frame; risk: number | null }[] {
  const byFrame = new Map(body.series.map((sample) => [sample.frame, sample]));
  return frames
    .filter((frame) => frame.kind === "forecast")
    .map((frame) => ({
      frame,
      risk: byFrame.get(frame.id)?.risk ?? null,
    }));
}

export function formatRisk(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(2);
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

/** "Sep 18" from an ISO date, in UTC so the label matches the data date. */
export function formatDate(iso: string | undefined): string {
  if (!iso) return "";
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function formatLongDate(iso: string | undefined): string {
  if (!iso) return "";
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function formatAge(ageDays: number | null | undefined): string {
  if (ageDays == null || !Number.isFinite(ageDays)) return "";
  if (ageDays < 1) return "<1 day old";
  return `${ageDays.toFixed(ageDays < 10 ? 1 : 0)} days old`;
}
