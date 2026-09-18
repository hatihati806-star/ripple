import { describe, expect, it } from "vitest";
import { RISK_GRADIENT, RISK_STOPS, decodeRisk, riskColor, riskRgb } from "./palette";

describe("risk palette", () => {
  it("runs cleaner-green to more-polluted-red", () => {
    expect(RISK_STOPS[0].value).toBe(0);
    expect(RISK_STOPS[0].color).toBe("#22C55E");
    expect(RISK_STOPS[RISK_STOPS.length - 1].color).toBe("#DC2626");
  });

  it("has ascending stop values", () => {
    const values = RISK_STOPS.map((stop) => stop.value);
    expect(values).toEqual([...values].sort((a, b) => a - b));
  });

  it("clamps out-of-range input", () => {
    expect(riskColor(-1)).toBe(riskColor(0));
    expect(riskColor(2)).toBe(riskColor(1));
  });

  it("treats non-finite input as the clean end", () => {
    expect(riskColor(Number.NaN)).toBe(riskColor(0));
  });

  it("returns distinct colours for low and high risk", () => {
    expect(riskColor(0)).not.toBe(riskColor(1));
  });

  it("becomes redder and less green as risk rises", () => {
    const parse = (value: number) => riskColor(value).match(/\d+/g)!.map(Number);
    const [rLow, gLow] = parse(0.1);
    const [rHigh, gHigh] = parse(0.95);
    expect(rHigh).toBeGreaterThan(rLow);
    expect(gHigh).toBeLessThan(gLow);
  });

  it("exposes a gradient covering the full 0-100% range", () => {
    expect(RISK_GRADIENT).toContain("0%");
    expect(RISK_GRADIENT).toContain("100%");
  });
});

describe("riskRgb", () => {
  it("matches the stop colours at the ends and mid ramp", () => {
    expect(riskRgb(0)).toEqual([34, 197, 94]);
    expect(riskRgb(1)).toEqual([220, 38, 38]);
    expect(riskRgb(0.35)).toEqual([163, 230, 53]);
    expect(riskRgb(0.55)).toEqual([250, 204, 21]);
  });

  it("clamps and treats non-finite input as the clean end", () => {
    expect(riskRgb(-3)).toEqual(riskRgb(0));
    expect(riskRgb(Number.NaN)).toEqual(riskRgb(0));
  });
});

describe("decodeRisk", () => {
  // Regression: the inverse lookup was built through riskColor's "rgb(...)" strings,
  // which hexToRgb could not parse -- the table was all black and every decoded pixel
  // came back as 0, so point sampling silently reported "cleanest" everywhere.
  it("recovers 0 and 1 from the exact ramp ends", () => {
    expect(decodeRisk(...riskRgb(0))).toBeCloseTo(0, 3);
    expect(decodeRisk(...riskRgb(1))).toBeCloseTo(1, 3);
  });

  it("recovers intermediate values within the 8-bit quantization", () => {
    for (const value of [0.1, 0.2, 0.35, 0.5, 0.55, 0.75, 0.9]) {
      expect(decodeRisk(...riskRgb(value))).toBeCloseTo(value, 1);
    }
  });
});
