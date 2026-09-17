import { describe, expect, it } from "vitest";
import {
  areaPath,
  continuousRuns,
  nearestIndex,
  polylinePath,
  rainBarHeights,
  riskSeriesToPoints,
  riskToPoint,
  splitAtBoundary,
  type ChartBox,
} from "./chartGeometry";

const BOX: ChartBox = { width: 300, height: 100, paddingTop: 10, paddingBottom: 10 };

describe("risk chart projection", () => {
  it("pins 0 to the baseline and 1 to the top of the plot area", () => {
    expect(riskToPoint(0, 0, 3, BOX).y).toBe(90);
    expect(riskToPoint(1, 0, 3, BOX).y).toBe(10);
  });

  it("places points evenly across the width, with no gap at the edges", () => {
    const points = riskSeriesToPoints([0.1, 0.2, 0.3, 0.4], BOX);
    expect(points[0].x).toBe(0);
    expect(points[3].x).toBe(300);
    expect(points[1].x).toBeCloseTo(100);
    expect(points[2].x).toBeCloseTo(200);
  });

  it("centres a single point instead of dividing by zero", () => {
    const [only] = riskSeriesToPoints([0.5], BOX);
    expect(only.x).toBe(150);
    expect(Number.isFinite(only.y)).toBe(true);
  });

  it("clamps out-of-range and non-finite values onto the axis", () => {
    expect(riskToPoint(-2, 0, 1, BOX).value).toBe(0);
    expect(riskToPoint(9, 0, 1, BOX).value).toBe(1);
    expect(riskToPoint(Number.NaN, 0, 1, BOX).value).toBe(0);
  });
});

describe("observed/forecast split", () => {
  const points = riskSeriesToPoints([0.2, 0.3, 0.4, 0.5], BOX);

  it("keeps the boundary point in both segments so the line is continuous", () => {
    const { observed, forecast } = splitAtBoundary(points, 1);
    expect(observed.map((p) => p.index)).toEqual([0, 1]);
    expect(forecast.map((p) => p.index)).toEqual([1, 2, 3]);
    expect(forecast[0]).toBe(observed[observed.length - 1]);
  });

  it("produces no forecast segment when the boundary is the last point", () => {
    const { observed, forecast } = splitAtBoundary(points, 3);
    expect(observed).toHaveLength(4);
    expect(forecast).toHaveLength(0);
  });

  it("returns both segments empty for no data", () => {
    expect(splitAtBoundary([], 2)).toEqual({ observed: [], forecast: [] });
  });
});

describe("continuousRuns", () => {
  const points = riskSeriesToPoints([0.1, 0.2, 0.3, 0.4, 0.5], BOX);

  it("keeps a contiguous series as one run", () => {
    const runs = continuousRuns(points, [true, true, true, true, true]);
    expect(runs).toHaveLength(1);
    expect(runs[0]).toHaveLength(5);
  });

  it("breaks the line at a coverage gap instead of bridging it", () => {
    const runs = continuousRuns(points, [true, true, false, true, true]);
    expect(runs.map((run) => run.length)).toEqual([2, 2]);
    expect(runs[0][1].index).toBe(1);
    expect(runs[1][0].index).toBe(3);
  });

  it("drops isolated points rather than drawing flat segments for them", () => {
    const runs = continuousRuns(points, [false, true, false, false, false]);
    expect(runs).toEqual([[points[1]]]);
  });

  it("returns nothing when no frame has a value", () => {
    expect(continuousRuns(points, [false, false, false, false, false])).toEqual([]);
  });
});

describe("chart helpers", () => {
  it("scales rain bars to the tallest value", () => {
    expect(rainBarHeights([0, 4, 8], 40)).toEqual([0, 20, 40]);
  });

  it("handles all-zero rain without dividing by zero", () => {
    expect(rainBarHeights([0, 0], 40)).toEqual([0, 0]);
  });

  it("ignores negative rain", () => {
    expect(rainBarHeights([-5, 10], 20)).toEqual([0, 20]);
  });

  it("snaps hover to the nearest sample", () => {
    expect(nearestIndex(0, BOX, 4)).toBe(0);
    expect(nearestIndex(99, BOX, 4)).toBe(1);
    expect(nearestIndex(100, BOX, 4)).toBe(1);
    expect(nearestIndex(1e6, BOX, 4)).toBe(3);
  });

  it("builds a closed area path and an open line path", () => {
    const points = riskSeriesToPoints([0.2, 0.8], BOX);
    expect(polylinePath(points).startsWith("M")).toBe(true);
    expect(polylinePath(points)).not.toContain("Z");
    expect(areaPath(points, 90).endsWith("Z")).toBe(true);
    expect(areaPath(points, 90).startsWith("M0.0,90.0")).toBe(true);
  });

  it("returns an empty area for no points", () => {
    expect(areaPath([], 90)).toBe("");
  });
});
