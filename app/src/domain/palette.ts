export interface RiskStop {
  value: number;
  color: string;
}

/**
 * Traffic-light risk ramp: green = cleaner, red = more polluted.
 *
 * The reference weather-radar image uses green->yellow->orange for precipitation
 * intensity, where green means *light rain*. Ripple reuses that visual language for a
 * different quantity, so the legend must always name the quantity and say which end is
 * cleaner. Green must never be read as "safe to drink".
 *
 * Must stay in sync with pipeline/ripple_pipeline/palette.py
 */
export const RISK_STOPS: RiskStop[] = [
  { value: 0.0, color: "#22C55E" },
  { value: 0.35, color: "#A3E635" },
  { value: 0.55, color: "#FACC15" },
  { value: 0.75, color: "#F97316" },
  { value: 1.0, color: "#DC2626" },
];

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** The ramp colour at `value` as an [r, g, b] triple, for canvas/3D consumers. */
export function riskRgb(value: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  for (let i = 0; i < RISK_STOPS.length - 1; i++) {
    const a = RISK_STOPS[i];
    const b = RISK_STOPS[i + 1];
    if (x >= a.value && x <= b.value) {
      const span = b.value - a.value || 1;
      const t = (x - a.value) / span;
      const ca = hexToRgb(a.color);
      const cb = hexToRgb(b.color);
      const mixed = ca.map((c, j) => Math.round(c + (cb[j] - c) * t));
      return [mixed[0], mixed[1], mixed[2]];
    }
  }
  return hexToRgb(RISK_STOPS[RISK_STOPS.length - 1].color);
}

/** Interpolate the ramp at a 0..1 value. Out-of-range input is clamped. */
export function riskColor(value: number): string {
  const [r, g, b] = riskRgb(value);
  return `rgb(${r}, ${g}, ${b})`;
}

export const RISK_GRADIENT = `linear-gradient(90deg, ${RISK_STOPS.map(
  (stop) => `${stop.color} ${stop.value * 100}%`,
).join(", ")})`;

export interface RiskBand {
  id: "clean" | "low" | "moderate" | "elevated" | "high";
  label: string;
  /** Representative colour for chips and dots. */
  color: string;
  /** One-line meaning, phrased as position within the observed range. */
  hint: string;
}

/**
 * Bands are cuts of the *relative* 0..1 index, not absolute water-quality classes.
 * They exist so a value reads as a phrase, never as a safety determination.
 */
export const RISK_BANDS: { max: number; band: RiskBand }[] = [
  {
    max: 0.2,
    band: {
      id: "clean",
      label: "Cleanest",
      color: "#16A34A",
      hint: "near the clean end of this region's observed range",
    },
  },
  {
    max: 0.45,
    band: {
      id: "low",
      label: "Low",
      color: "#65A30D",
      hint: "below the region's typical level",
    },
  },
  {
    max: 0.65,
    band: {
      id: "moderate",
      label: "Moderate",
      color: "#CA8A04",
      hint: "around the region's typical level",
    },
  },
  {
    max: 0.85,
    band: {
      id: "elevated",
      label: "Elevated",
      color: "#EA580C",
      hint: "well above the region's typical level",
    },
  },
  {
    max: Infinity,
    band: {
      id: "high",
      label: "Highest",
      color: "#DC2626",
      hint: "among the most polluted pixels in the region right now",
    },
  },
];

export function riskBand(value: number | null | undefined): RiskBand | null {
  if (value == null || !Number.isFinite(value)) return null;
  const x = Math.min(1, Math.max(0, value));
  return (RISK_BANDS.find((entry) => x <= entry.max) ?? RISK_BANDS[RISK_BANDS.length - 1])
    .band;
}

const RAMP_LOOKUP_SIZE = 512;
// Built from the RGB triple directly: `riskColor` returns "rgb(...)" strings that
// `hexToRgb` cannot parse, and silently produced an all-black table -- every lookup then
// returned the nearest black entry, so `decodeRisk` reported 0 ("cleanest") for any pixel.
const RAMP_LOOKUP: [number, number, number][] = Array.from(
  { length: RAMP_LOOKUP_SIZE },
  (_, index) => riskRgb(index / (RAMP_LOOKUP_SIZE - 1)),
);

/**
 * Invert the ramp: nearest ramp colour for an RGB triple, as a 0..1 risk value.
 *
 * Used to read values back out of a tile the way the browser sees it. The tile is
 * 8-bit, so the result is a quantized estimate of the same value the pipeline rendered;
 * it is shown as a band rather than a precise number.
 */
export function decodeRisk(r: number, g: number, b: number): number {
  let bestIndex = 0;
  let bestDistance = Infinity;
  for (let index = 0; index < RAMP_LOOKUP_SIZE; index++) {
    const [cr, cg, cb] = RAMP_LOOKUP[index];
    const dr = r - cr;
    const dg = g - cg;
    const db = b - cb;
    const distance = dr * dr + dg * dg + db * db;
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
      if (distance === 0) break;
    }
  }
  return bestIndex / (RAMP_LOOKUP_SIZE - 1);
}
