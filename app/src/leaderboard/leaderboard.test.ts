import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Manifest } from "../domain/types";
import { assessBody, countDemoted, rankBodies } from "../water/ranking";

/**
 * The shipped manifest, ranked the way the leaderboard ranks it.
 *
 * These assertions exist because the two failures they encode were real and shipped: a
 * four-way tie at a saturated 1.00, and a leaderboard whose top two entries were its two
 * least defensible readings. They are checked against the delivered data rather than a
 * fixture, so a future pipeline run that reintroduces a peak-driven headline fails here.
 */
const here = dirname(fileURLToPath(import.meta.url));
const manifestPath = resolve(here, "../../public/data/manifest.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as Manifest;
const bodies = manifest.water_bodies ?? [];

describe("shipped manifest: the ranked reading is a body mean", () => {
  it("carries the fields the ranking depends on", () => {
    expect(manifest.schema_version).toBeGreaterThanOrEqual(3);
    expect(manifest.body_statistic).toMatch(/areal mean/i);
    const measured = bodies.filter((body) => body.latest_risk != null);
    expect(measured.length).toBeGreaterThan(200);
    for (const body of measured) {
      expect(body.latest_peak).not.toBeUndefined();
      expect(body.latest_cells).toBeGreaterThan(0);
    }
  });

  it("has no saturated pile-up at exactly 1.00", () => {
    const saturated = bodies.filter(
      (body) => body.latest_risk != null && body.latest_risk >= 0.9995,
    );
    expect(saturated.length).toBeLessThanOrEqual(1);
  });

  it("keeps every body's peak at or above its mean", () => {
    for (const body of bodies) {
      if (body.latest_risk == null || body.latest_peak == null) continue;
      expect(body.latest_peak).toBeGreaterThanOrEqual(body.latest_risk - 1e-9);
    }
  });

  it("does not lead with a body whose reading is demoted", () => {
    const ranked = rankBodies(bodies, "dirtiest");
    expect(ranked.length).toBeGreaterThan(0);
    const demoting = (entry: (typeof ranked)[number]) =>
      entry.flags.filter((flag) => flag.demotes);
    expect(demoting(ranked[0])).toEqual([]);
    expect(demoting(ranked[1])).toEqual([]);
    // A body at the top of a relative ramp may still be flagged as saturated: that is a
    // caveat on the reading, not a reason to distrust it as a reading of the body.
    expect(ranked[0].flags.every((flag) => flag.kind === "saturated")).toBe(true);
  });

  it("no longer leads with Georgian Bay or Great Salt Lake", () => {
    const top = rankBodies(bodies, "dirtiest")
      .slice(0, 2)
      .map((entry) => entry.body.name);
    expect(top).not.toContain("Georgian Bay");
    expect(top).not.toContain("Great Salt Lake");
  });

  it("flags Georgian Bay as peak-driven and Great Salt Lake as a caution", () => {
    const georgianBay = bodies.find((body) => body.name === "Georgian Bay");
    const salt = bodies.find((body) => body.name === "Great Salt Lake");
    expect(georgianBay).toBeDefined();
    expect(salt).toBeDefined();
    expect(assessBody(georgianBay!).map((flag) => flag.kind)).toContain("peak-driven");
    expect(assessBody(salt!).map((flag) => flag.kind)).toContain("caution");
  });

  it("places both of them below the ranked readings", () => {
    const ranked = rankBodies(bodies, "dirtiest");
    const firstRanked = ranked.findIndex((entry) =>
      entry.flags.some((flag) => flag.demotes),
    );
    const georgianBayIndex = ranked.findIndex(
      (entry) => entry.body.name === "Georgian Bay",
    );
    const saltIndex = ranked.findIndex((entry) => entry.body.name === "Great Salt Lake");
    expect(georgianBayIndex).toBeGreaterThanOrEqual(firstRanked);
    expect(saltIndex).toBeGreaterThanOrEqual(firstRanked);
  });

  it("reports how many readings are set aside", () => {
    const demoted = countDemoted(bodies);
    expect(demoted).toBeGreaterThan(0);
    expect(demoted).toBeLessThan(bodies.length / 2);
  });

  it("keeps the peak beside the mean so the worst cell is still visible", () => {
    const georgianBay = bodies.find((body) => body.name === "Georgian Bay");
    expect(georgianBay!.latest_peak).toBeCloseTo(1.0, 3);
    expect(georgianBay!.latest_risk).toBeLessThan(0.6);
    expect(georgianBay!.latest_cells).toBeGreaterThan(1000);
  });
});
