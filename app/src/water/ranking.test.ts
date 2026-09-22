import { describe, expect, it } from "vitest";
import {
  assessBody,
  bloomFlag,
  bodyForecast,
  bodyHistory,
  countDemoted,
  countSampled,
  formatAge,
  formatDate,
  formatPercent,
  formatRisk,
  latestAvailable,
  rankBodies,
  riskAtFrame,
} from "./ranking";
import type { Frame, WaterBody } from "../domain/types";

function body(
  id: string,
  latest_risk: number | null,
  series: WaterBody["series"] = [],
  extras: Partial<WaterBody> = {},
): WaterBody {
  return {
    id,
    name: id,
    kind: "lake",
    lat: 50,
    lon: -97,
    area_km2: 120,
    curated: true,
    series,
    rain_mm: [],
    latest_risk,
    latest_ndci: 0.1,
    latest_ndti: -0.2,
    observed_frames: series.length,
    total_frames: series.length,
    ...extras,
  };
}

function frame(id: string, kind: "observed" | "forecast"): Frame {
  return {
    id,
    kind,
    label: id,
    window_label: id,
    date: "2026-09-16",
    tile: `${id}.png`,
    probe_tile: `${id}-probe.png`,
    latest: false,
    age_days: 2,
  };
}

describe("rankBodies", () => {
  const bodies = [
    body("a", 0.2),
    body("b", 0.9),
    body("c", null),
    body("d", 0.55),
  ];

  it("ranks the dirtiest first and drops bodies with no reading", () => {
    const ranked = rankBodies(bodies, "dirtiest");
    expect(ranked.map((entry) => entry.body.id)).toEqual(["b", "d", "a"]);
    expect(countSampled(bodies)).toBe(3);
  });

  it("can rank the cleanest first", () => {
    expect(rankBodies(bodies, "cleanest").map((entry) => entry.body.id)).toEqual([
      "a",
      "d",
      "b",
    ]);
  });

  it("attaches a band to every ranked body", () => {
    for (const entry of rankBodies(bodies)) {
      expect(entry.band.label.length).toBeGreaterThan(0);
    }
  });
});

describe("assessBody", () => {
  it("says nothing about a well-sampled body whose mean and peak agree", () => {
    const clean = body("a", 0.4, [], {
      latest_peak: 0.45,
      latest_cells: 900,
      latest_cell_fraction: 0.98,
    });
    expect(assessBody(clean)).toEqual([]);
  });

  it("flags a body whose headline comes from a minority of its own water", () => {
    // Georgian Bay: 2743 sampled cells, mean 0.49, one saturated cell.
    const georgianBay = body("georgian-bay", 0.493, [], {
      latest_peak: 1.0,
      latest_cells: 2743,
      latest_cell_fraction: 0.968,
    });
    const flags = assessBody(georgianBay);
    expect(flags.map((flag) => flag.kind)).toEqual(["peak-driven"]);
    expect(flags[0].demotes).toBe(true);
    expect(flags[0].hint).toContain("1.00");
    expect(flags[0].hint).toContain("0.49");
  });

  it("does not flag a body whose peak is close to its mean", () => {
    const tight = body("a", 0.9, [], {
      latest_peak: 1.0,
      latest_cells: 500,
      latest_cell_fraction: 1.0,
    });
    expect(assessBody(tight)).toEqual([]);
  });

  it("flags a thin sample by cell count and by footprint share", () => {
    const fewCells = body("a", 0.5, [], { latest_peak: 0.5, latest_cells: 6 });
    expect(assessBody(fewCells).map((flag) => flag.kind)).toContain("thin-sample");
    const partial = body("b", 0.5, [], {
      latest_peak: 0.5,
      latest_cells: 400,
      latest_cell_fraction: 0.1,
    });
    expect(assessBody(partial).map((flag) => flag.kind)).toContain("thin-sample");
  });

  it("carries a catalogued caution and demotes on it", () => {
    const salt = body("great-salt-lake", 0.97, [], {
      latest_peak: 1.0,
      latest_cells: 610,
      latest_cell_fraction: 0.993,
      caution: "Hypersaline: mineral and halophilic colour dominates the spectrum",
    });
    const flags = assessBody(salt);
    expect(flags.map((flag) => flag.kind)).toEqual(["caution"]);
    expect(flags[0].demotes).toBe(true);
  });

  it("flags saturation without demoting it", () => {
    const saturated = body("a", 1.0, [], {
      latest_peak: 1.0,
      latest_cells: 72,
      latest_cell_fraction: 0.99,
    });
    const flags = assessBody(saturated);
    expect(flags.map((flag) => flag.kind)).toEqual(["saturated"]);
    expect(flags[0].demotes).toBe(false);
  });
});

describe("rankBodies ordering and demotion", () => {
  const ordinary = (id: string, risk: number) =>
    body(id, risk, [], { latest_peak: risk + 0.02, latest_cells: 500 });

  it("ranks by the mean, not the peak", () => {
    // b has the highest peak but a lower mean than a: a must win.
    const a = body("a", 0.70, [], { latest_peak: 0.72, latest_cells: 500 });
    const b = body("b", 0.30, [], { latest_peak: 1.0, latest_cells: 500 });
    expect(rankBodies([b, a], "dirtiest").map((entry) => entry.body.id)).toEqual(["a", "b"]);
  });

  it("lists demoted bodies after ranked ones in the dirtiest order", () => {
    const flagged = body("flagged", 0.99, [], {
      latest_peak: 1.0,
      latest_cells: 4, // thin sample
    });
    const ranked = rankBodies([flagged, ordinary("clean", 0.2)], "dirtiest");
    expect(ranked.map((entry) => entry.body.id)).toEqual(["clean", "flagged"]);
  });

  it("lists demoted bodies after ranked ones in the cleanest order too", () => {
    const flagged = body("flagged", 0.01, [], {
      latest_peak: 1.0,
      latest_cells: 4,
    });
    const ranked = rankBodies([flagged, ordinary("dirty", 0.8)], "cleanest");
    expect(ranked.map((entry) => entry.body.id)).toEqual(["dirty", "flagged"]);
  });

  it("keeps flagged bodies ordered among themselves by the mean", () => {
    const worse = body("worse", 0.8, [], { latest_cells: 3 });
    const better = body("better", 0.5, [], { latest_cells: 3 });
    expect(rankBodies([better, worse], "dirtiest").map((entry) => entry.body.id)).toEqual([
      "worse",
      "better",
    ]);
  });

  it("breaks an exact tie deterministically by name", () => {
    const ranked = rankBodies([ordinary("zebra", 0.5), ordinary("alpha", 0.5)], "dirtiest");
    expect(ranked.map((entry) => entry.body.id)).toEqual(["alpha", "zebra"]);
  });

  it("exposes the peak and the sampled cells beside the ranked mean", () => {
    const entry = rankBodies([
      body("a", 0.4, [], { latest_peak: 0.9, latest_cells: 123, latest_cell_fraction: 0.5 }),
    ])[0];
    expect(entry.risk).toBe(0.4);
    expect(entry.peak).toBe(0.9);
    expect(entry.cells).toBe(123);
    expect(entry.cellFraction).toBe(0.5);
  });

  it("counts the demoted readings for the footer", () => {
    const bodies = [
      ordinary("a", 0.5),
      body("b", 0.5, [], { latest_cells: 2 }),
      body("c", 0.5, [], { caution: "hypersaline" }),
      body("d", null),
    ];
    expect(countSampled(bodies)).toBe(3);
    expect(countDemoted(bodies)).toBe(2);
  });
});

describe("series alignment", () => {
  const frames = [frame("obs-00", "observed"), frame("obs-01", "observed"), frame("fc-01", "forecast")];
  const withSeries = body("x", 0.4, [
    { frame: "obs-00", risk: 0.1, valid: true },
    { frame: "obs-01", risk: 0.4, valid: true },
    { frame: "fc-01", risk: 0.6, valid: true },
  ]);

  it("aligns observed history with the observed frames only", () => {
    const history = bodyHistory(withSeries, frames);
    expect(history.map((entry) => entry.frame.id)).toEqual(["obs-00", "obs-01"]);
    expect(history.map((entry) => entry.risk)).toEqual([0.1, 0.4]);
  });

  it("aligns forecast values with the forecast frames only", () => {
    const forecast = bodyForecast(withSeries, frames);
    expect(forecast.map((entry) => entry.frame.id)).toEqual(["fc-01"]);
    expect(forecast[0].risk).toBe(0.6);
  });

  it("reports null for frames the body has no sample on", () => {
    const sparse = body("y", 0.2, [{ frame: "obs-01", risk: 0.2, valid: true }]);
    expect(bodyHistory(sparse, frames).map((entry) => entry.risk)).toEqual([null, 0.2]);
  });
});

describe("riskAtFrame", () => {
  const withSeries = body("x", 0.4, [
    { frame: "obs-00", risk: 0.1, valid: true },
    { frame: "obs-01", risk: null, valid: false },
    { frame: "fc-01", risk: 0.6, valid: true },
  ]);

  it("returns the reading for the requested frame only", () => {
    expect(riskAtFrame(withSeries, "obs-00")).toBe(0.1);
    expect(riskAtFrame(withSeries, "fc-01")).toBe(0.6);
  });

  it("returns null when the frame has no valid sample or is unknown", () => {
    expect(riskAtFrame(withSeries, "obs-01")).toBeNull();
    expect(riskAtFrame(withSeries, "obs-99")).toBeNull();
  });
});

describe("latestAvailable", () => {
  const frames = [
    frame("obs-00", "observed"),
    frame("obs-01", "observed"),
    frame("fc-01", "forecast"),
  ];

  it("falls back to the newest observed frame with a valid reading", () => {
    const body = { ...frame, latest_risk: null } as unknown as WaterBody;
    const measured: WaterBody = {
      ...body,
      series: [
        { frame: "obs-00", risk: 0.2, ndci: 0.01, ndti: -0.3, valid: true },
        { frame: "obs-01", risk: null, valid: false },
        { frame: "fc-01", risk: 0.5, valid: true },
      ],
    };
    const reading = latestAvailable(measured, frames);
    expect(reading?.frame.id).toBe("obs-00");
    expect(reading?.risk).toBe(0.2);
    expect(reading?.ndci).toBe(0.01);
  });

  it("never falls back to a forecast frame", () => {
    const body: WaterBody = {
      ...(frame as unknown as WaterBody),
      id: "x",
      name: "x",
      series: [{ frame: "fc-01", risk: 0.5, valid: true }],
    };
    expect(latestAvailable(body, frames)).toBeNull();
  });

  it("returns null when nothing was ever measured", () => {
    const body: WaterBody = { ...(frame as unknown as WaterBody), id: "y", name: "y", series: [] };
    expect(latestAvailable(body, frames)).toBeNull();
  });
});

describe("bloomFlag", () => {
  it("flags NDCI above the published threshold and never claims toxicity", () => {
    const flagged = bloomFlag(0.31, 0.1);
    expect(flagged?.label).toBe("Probable bloom");
    expect(flagged?.hint).toContain("0.1");
    expect(flagged?.hint.toLowerCase()).not.toContain("toxic");
  });

  it("reports values under the threshold as below it", () => {
    expect(bloomFlag(-0.07, 0.1)?.label).toBe("Below bloom threshold");
  });

  it("is silent without a value or a threshold", () => {
    expect(bloomFlag(null, 0.1)).toBeNull();
    expect(bloomFlag(0.3, undefined)).toBeNull();
    expect(bloomFlag(Number.NaN, 0.1)).toBeNull();
  });
});

describe("formatting", () => {
  it("formats risk to two decimals and dashes for missing", () => {
    expect(formatRisk(0.4567)).toBe("0.46");
    expect(formatRisk(null)).toBe("—");
    expect(formatRisk(Number.NaN)).toBe("—");
  });

  it("formats percentages", () => {
    expect(formatPercent(0.1589)).toBe("15.9%");
    expect(formatPercent(null)).toBe("—");
  });

  it("formats ISO dates in UTC so the label matches the data", () => {
    expect(formatDate("2026-09-18")).toBe("Sep 18");
    expect(formatDate(undefined)).toBe("");
  });

  it("formats observation age", () => {
    expect(formatAge(0.4)).toBe("<1 day old");
    expect(formatAge(3.21)).toBe("3.2 days old");
    expect(formatAge(14)).toBe("14 days old");
    expect(formatAge(null)).toBe("");
  });
});
