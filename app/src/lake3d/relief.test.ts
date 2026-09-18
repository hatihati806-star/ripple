import { describe, expect, it } from "vitest";
import {
  MAX_HALF_KM,
  MIN_HALF_KM,
  WATER_ALPHA,
  buildSurface,
  cropWindow,
  decodeRelief,
  halfExtentKm,
  smoothRelief,
} from "./relief";
import { lonLatToMercator, type PixelGrid } from "../map/projection";

/** A grid shaped like the real overlay: 4096x2777 square 1848 m pixels over EPSG:3857. */
const [left, bottom] = lonLatToMercator(-128, 18);
const [right, top] = lonLatToMercator(-60, 54);
const GRID: PixelGrid = { width: 4096, height: 2777, mercBounds: [left, bottom, right, top] };

function pixels(rows: number[][]): Uint8ClampedArray {
  return new Uint8ClampedArray(rows.flat());
}

const GREEN = [34, 197, 94, 190];
const RED = [220, 38, 38, 190];
const CLEAR = [0, 0, 0, 0];

describe("halfExtentKm", () => {
  it("floors tiny and unknown bodies so the crop stays legible", () => {
    expect(halfExtentKm(20)).toBe(MIN_HALF_KM);
    expect(halfExtentKm(null)).toBe(MIN_HALF_KM);
    expect(halfExtentKm(Number.NaN)).toBe(MIN_HALF_KM);
  });

  it("scales with the equivalent radius and caps at the maximum", () => {
    const superior = halfExtentKm(57328);
    expect(superior).toBeGreaterThan(MIN_HALF_KM);
    expect(superior).toBeLessThan(MAX_HALF_KM);
    expect(halfExtentKm(3_000_000)).toBe(MAX_HALF_KM);
  });

  it("is monotonic in area", () => {
    expect(halfExtentKm(1000)).toBeLessThan(halfExtentKm(10_000));
  });
});

describe("cropWindow", () => {
  it("centres on the body and keeps a square-ish ground window", () => {
    const crop = cropWindow(-89.5, 47.5, 100, GRID);
    const [w, , e] = crop.lonLat;
    expect((w + e) / 2).toBeCloseTo(-89.5, 1);
    expect(crop.pixels.width).toBeGreaterThan(100);
    expect(crop.pixels.height).toBeGreaterThan(100);
    expect(crop.pixels.width / crop.pixels.height).toBeGreaterThan(0.9);
    expect(crop.pixels.width / crop.pixels.height).toBeLessThan(1.1);
    expect(Math.abs(crop.extentXKm - 200)).toBeLessThan(4);
    expect(Math.abs(crop.extentZKm - 200)).toBeLessThan(4);
  });

  it("clamps to the raster instead of sampling outside it", () => {
    const crop = cropWindow(-128, 20, 300, GRID);
    expect(crop.pixels.x).toBeGreaterThanOrEqual(0);
    expect(crop.pixels.y).toBeGreaterThanOrEqual(0);
    expect(crop.pixels.x + crop.pixels.width).toBeLessThanOrEqual(GRID.width);
    expect(crop.pixels.y + crop.pixels.height).toBeLessThanOrEqual(GRID.height);
    expect(crop.pixels.width).toBeGreaterThan(0);
    expect(crop.pixels.height).toBeGreaterThan(0);
  });
});

describe("decodeRelief", () => {
  it("decodes the ramp ends to 0 and 1 and rejects transparent pixels", () => {
    const data = pixels([GREEN, RED, CLEAR, GREEN]);
    const { risk, valid } = decodeRelief(data, 2, 2);
    expect(valid[0]).toBe(1);
    expect(risk[0]).toBeCloseTo(0, 5);
    expect(valid[1]).toBe(1);
    expect(risk[1]).toBeCloseTo(1, 2);
    expect(valid[2]).toBe(0);
    expect(risk[2]).toBe(0);
  });

  it("treats alpha below the water threshold as land/no-data", () => {
    const faint = [220, 38, 38, WATER_ALPHA - 1];
    const { valid, risk } = decodeRelief(pixels([faint]), 1, 1);
    expect(valid[0]).toBe(0);
    expect(risk[0]).toBe(0);
  });

  it("never emits NaN or out-of-range values", () => {
    const data = pixels([GREEN, RED, CLEAR, [128, 128, 128, 200], [0, 0, 0, 255], RED]);
    const { risk, valid } = decodeRelief(data, 3, 2);
    for (let index = 0; index < risk.length; index++) {
      expect(Number.isFinite(risk[index])).toBe(true);
      expect(risk[index]).toBeGreaterThanOrEqual(0);
      expect(risk[index]).toBeLessThanOrEqual(1);
      expect([0, 1]).toContain(valid[index]);
    }
  });
});

describe("buildSurface", () => {
  const relief = (data: Uint8ClampedArray, width: number, height: number) =>
    decodeRelief(data, width, height);

  it("builds indexed geometry with one height per valid vertex", () => {
    // 2x2: green (0), red (1), clear, green
    const r = relief(pixels([GREEN, RED, CLEAR, GREEN]), 2, 2);
    const surface = buildSurface(r, 200, 200, 10);
    expect(surface.vertexCount).toBe(4);
    expect(surface.quadCount).toBe(1);
    expect(surface.indices.length).toBe(6);
    const y = (index: number) => surface.positions[index * 3 + 1];
    expect(y(0)).toBeCloseTo(0, 5); // green
    expect(y(1)).toBeCloseTo(10, 1); // red
    expect(y(2)).toBeCloseTo(0, 5); // land corner: flat
    expect(y(3)).toBeCloseTo(0, 5);
  });

  it("keeps every position and colour finite", () => {
    const r = relief(pixels([GREEN, RED, CLEAR, GREEN, RED, RED, CLEAR, CLEAR, GREEN]), 3, 3);
    const surface = buildSurface(r, 150, 90, 18);
    for (const value of surface.positions) expect(Number.isFinite(value)).toBe(true);
    for (const value of surface.colors) expect(Number.isFinite(value)).toBe(true);
    for (const index of surface.indices) {
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(surface.vertexCount);
    }
  });

  it("emits no quads when there is no water at all", () => {
    const r = relief(pixels([CLEAR, CLEAR, CLEAR, CLEAR]), 2, 2);
    const surface = buildSurface(r, 100, 100, 10);
    expect(surface.indices.length).toBe(0);
    expect(surface.peak).toBeNull();
    expect(surface.meanRisk).toBeNull();
    expect(surface.waterFraction).toBe(0);
  });

  it("includes a quad when at least two corners are water, so shorelines do not tear", () => {
    const r = relief(pixels([RED, CLEAR, RED, CLEAR]), 2, 2);
    const surface = buildSurface(r, 100, 100, 10);
    expect(surface.quadCount).toBe(1);
  });

  it("drops quads with a single water corner, so stray pixels do not become spikes", () => {
    const r = relief(pixels([RED, CLEAR, CLEAR, CLEAR]), 2, 2);
    const surface = buildSurface(r, 100, 100, 10);
    expect(surface.quadCount).toBe(0);
  });

  it("reports the risk peak, mean and water fraction over valid vertices only", () => {
    const r = relief(pixels([GREEN, RED, CLEAR, RED]), 2, 2);
    const surface = buildSurface(r, 100, 100, 10);
    expect(surface.peak?.risk).toBeCloseTo(1, 2);
    expect(surface.peak?.col).toBe(1);
    expect(surface.peak?.row).toBe(0);
    expect(surface.meanRisk).toBeCloseTo((0 + 1 + 1) / 3, 2);
    expect(surface.waterFraction).toBeCloseTo(3 / 4, 6);
  });

  it("survives degenerate grids without NaN", () => {
    const single = buildSurface(relief(pixels([RED]), 1, 1), 50, 50, 5);
    expect(single.indices.length).toBe(0);
    expect(single.peak?.risk).toBeCloseTo(1, 2);
    const empty = buildSurface(
      { width: 0, height: 0, risk: new Float32Array(0), valid: new Uint8Array(0) },
      0,
      0,
      5,
    );
    expect(empty.positions.length).toBe(0);
    expect(empty.peak).toBeNull();
  });

  it("produces darker linear colours for high risk than for low risk", () => {
    const r = relief(pixels([GREEN, RED, CLEAR, GREEN]), 2, 2);
    const surface = buildSurface(r, 100, 100, 10);
    const greenLuma = surface.colors[0] + surface.colors[1] * 2 + surface.colors[2];
    const redLuma = surface.colors[3] + surface.colors[4] * 2 + surface.colors[5];
    expect(redLuma).toBeLessThan(greenLuma);
  });
});

describe("smoothRelief", () => {
  it("pulls a lone spike towards its neighbours without leaving their range", () => {
    const r = decodeRelief(
      pixels([GREEN, GREEN, GREEN, GREEN, RED, GREEN, GREEN, GREEN, GREEN]),
      3,
      3,
    );
    const smoothed = smoothRelief(r);
    const centre = smoothed.risk[4];
    expect(centre).toBeLessThan(r.risk[4]);
    expect(centre).toBeGreaterThan(0);
    for (let index = 0; index < smoothed.risk.length; index++) {
      expect(Number.isFinite(smoothed.risk[index])).toBe(true);
      expect(smoothed.risk[index]).toBeGreaterThanOrEqual(0);
      expect(smoothed.risk[index]).toBeLessThanOrEqual(1);
    }
  });

  it("keeps invalid pixels invalid and untouched", () => {
    const r = decodeRelief(
      pixels([RED, CLEAR, RED, CLEAR, CLEAR, CLEAR, RED, CLEAR, RED]),
      3,
      3,
    );
    const smoothed = smoothRelief(r);
    expect(Array.from(smoothed.valid)).toEqual(Array.from(r.valid));
    expect(smoothed.risk[4]).toBe(0);
  });

  it("is a no-op when nothing is valid", () => {
    const r = decodeRelief(pixels([CLEAR, CLEAR, CLEAR, CLEAR]), 2, 2);
    const smoothed = smoothRelief(r);
    expect(Array.from(smoothed.valid)).toEqual([0, 0, 0, 0]);
    expect(Array.from(smoothed.risk)).toEqual([0, 0, 0, 0]);
  });
});
