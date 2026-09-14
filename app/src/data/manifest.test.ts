import { describe, expect, it } from "vitest";
import { DEFAULT_DISCLAIMER, normalizeManifest } from "./manifest";

const valid = {
  generated_utc: "2026-09-14T00:00:00Z",
  region_bbox: [-106, 40, -82, 54],
  resolution_m: 652,
  palette: [],
  scales: { ndci: [0, 0.1, 0.3], ndti: [0.05, 0.15] },
  frames: [
    { id: "obs-latest", kind: "observed", label: "A", tile: "a.png", age_days: 2 },
  ],
  disclaimer: "x",
};

describe("normalizeManifest", () => {
  it("rejects payloads missing required fields", () => {
    expect(normalizeManifest({})).toBeNull();
    expect(normalizeManifest(null)).toBeNull();
    expect(normalizeManifest(undefined)).toBeNull();
    expect(normalizeManifest("nope")).toBeNull();
  });

  it("rejects a payload whose frames are not an array", () => {
    expect(normalizeManifest({ ...valid, frames: "x" })).toBeNull();
  });

  it("accepts a well-formed manifest", () => {
    const m = normalizeManifest(valid);
    expect(m?.frames).toHaveLength(1);
    expect(m?.resolution_m).toBe(652);
  });

  it("defaults a missing disclaimer rather than rendering undefined", () => {
    const { disclaimer, ...withoutDisclaimer } = valid;
    void disclaimer;
    const m = normalizeManifest(withoutDisclaimer);
    expect(m?.disclaimer).toBe(DEFAULT_DISCLAIMER);
    expect(m?.disclaimer).toMatch(/not a regulatory measurement/i);
  });

  it("preserves coverage and baseline metadata when present", () => {
    const m = normalizeManifest({
      ...valid,
      coverage: {
        satellite_valid_fraction: 0.195,
        water_fraction_of_grid: 0.0177,
        scenes_mosaicked: 85,
      },
      frames: [
        {
          ...valid.frames[0],
          ndti_baseline: [-0.5, -0.07],
          bloom_fraction: 0.12,
        },
      ],
    });
    expect(m?.coverage?.scenes_mosaicked).toBe(85);
    expect(m?.frames[0].bloom_fraction).toBeCloseTo(0.12);
  });
});
