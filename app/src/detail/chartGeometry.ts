export type Confidence = "high" | "medium" | "low" | "none";

/** Observation age in days to a confidence level. Fresh composites are trustworthy. */
export function confidenceFromAge(ageDays: number | null | undefined): Confidence {
  if (ageDays == null || !Number.isFinite(ageDays)) return "none";
  if (ageDays <= 3) return "high";
  if (ageDays <= 10) return "medium";
  return "low";
}

export interface XY {
  x: number;
  y: number;
}

/** Map a value series to points in a `width` x `height` box, y inverted. */
export function seriesToPoints(values: number[], width: number, height: number): XY[] {
  if (values.length === 0) return [];
  const max = Math.max(...values, 1e-6);
  const step = values.length === 1 ? 0 : width / (values.length - 1);
  return values.map((value, index) => ({
    x: Math.round(index * step),
    y: Math.round(height - (value / max) * height),
  }));
}

/** SVG path through a value series, spanning the full width of the box. */
export function chartPath(values: number[], width: number, height: number): string {
  return seriesToPoints(values, width, height)
    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x},${point.y}`)
    .join(" ");
}

export interface ChartPoint extends XY {
  index: number;
  value: number;
}

export interface ChartBox {
  width: number;
  height: number;
  paddingTop: number;
  paddingBottom: number;
}

/**
 * Fixed-scale projection for the risk chart.
 *
 * The risk index is already normalized to 0..1, so the axis is pinned to that range
 * rather than autoscaled to the data: an autoscaled axis would make a lake sitting at
 * 0.05 look as dramatic as one at 0.95.
 */
export function riskToPoint(
  value: number,
  index: number,
  count: number,
  box: ChartBox,
): ChartPoint {
  const usable = Math.max(1, box.height - box.paddingTop - box.paddingBottom);
  const clamped = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  const step = count <= 1 ? 0 : box.width / (count - 1);
  return {
    x: count <= 1 ? box.width / 2 : index * step,
    y: box.paddingTop + (1 - clamped) * usable,
    index,
    value: clamped,
  };
}

export function riskSeriesToPoints(values: number[], box: ChartBox): ChartPoint[] {
  return values.map((value, index) => riskToPoint(value, index, values.length, box));
}

/** Split a projected series at `boundary` (index of the last observed point). */
export function splitAtBoundary(
  points: ChartPoint[],
  boundary: number,
): { observed: ChartPoint[]; forecast: ChartPoint[] } {
  if (points.length === 0) return { observed: [], forecast: [] };
  const cut = Math.min(Math.max(0, boundary), points.length - 1);
  return {
    observed: points.slice(0, cut + 1),
    // The forecast line starts at the last observation so the two segments connect.
    forecast: cut >= 0 && cut < points.length - 1 ? points.slice(cut) : [],
  };
}

export function polylinePath(points: ChartPoint[]): string {
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)},${point.y.toFixed(1)}`)
    .join(" ");
}

/**
 * Split a projected series into contiguous runs of real values.
 *
 * A frame with no valid reading is a coverage gap, so the line must break there: joining
 * across it would draw a straight segment through weeks of missing data and imply
 * measurements that do not exist.
 */
export function continuousRuns(
  points: ChartPoint[],
  hasValue: boolean[],
): ChartPoint[][] {
  const runs: ChartPoint[][] = [];
  let current: ChartPoint[] = [];
  points.forEach((point, index) => {
    if (hasValue[index]) {
      current.push(point);
    } else if (current.length > 0) {
      runs.push(current);
      current = [];
    }
  });
  if (current.length > 0) runs.push(current);
  return runs;
}

export function areaPath(points: ChartPoint[], baselineY: number): string {
  if (points.length === 0) return "";
  const first = points[0];
  const last = points[points.length - 1];
  return (
    `M${first.x.toFixed(1)},${baselineY.toFixed(1)} ` +
    points.map((point) => `L${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ") +
    ` L${last.x.toFixed(1)},${baselineY.toFixed(1)} Z`
  );
}

/** Scale rain bars to the chart box; returns per-point heights in pixels. */
export function rainBarHeights(rain: number[], maxHeight: number): number[] {
  const peak = Math.max(...rain, 1e-6);
  return rain.map((value) =>
    Math.max(0, Math.round((Math.max(0, value) / peak) * maxHeight)),
  );
}

/** Nearest point index to a horizontal position, for hover readouts. */
export function nearestIndex(x: number, box: ChartBox, count: number): number {
  if (count <= 1) return 0;
  const step = box.width / (count - 1);
  return Math.min(count - 1, Math.max(0, Math.round(x / step)));
}

/** How much of the 0..1 axis to label. Keeps the axis honest without clutter. */
export const RISK_AXIS_TICKS = [0, 0.5, 1] as const;
