import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  CLIENT_ACTIONS, frameLabel, outlookFor, rankBodies, resolveBody, resolveFrame,
} from "./actions";
import type { FactBase } from "./types";

const here = dirname(fileURLToPath(import.meta.url));
const facts = JSON.parse(
  readFileSync(resolve(here, "../../public/data/assistant.json"), "utf-8"),
) as FactBase;

describe("resolveBody", () => {
  it("resolves an exact catalogue name", () => {
    expect(resolveBody(facts, "Lake Erie")?.id).toBe("lake-erie");
    expect(resolveBody(facts, "Great Salt Lake")?.id).toBe("great-salt-lake");
  });

  it("resolves case-insensitively and by id", () => {
    expect(resolveBody(facts, "lake erie")?.id).toBe("lake-erie");
    expect(resolveBody(facts, "lake-superior")?.id).toBe("lake-superior");
  });

  it("resolves a distinctive short name the way a person would say it", () => {
    expect(resolveBody(facts, "erie")?.id).toBe("lake-erie");
    expect(resolveBody(facts, "okeechobee")?.id).toBe("lake-okeechobee");
  });

  it("refuses a body that is not in the catalogue", () => {
    // The important case: the map must never fly to a lake that is not in the data.
    expect(resolveBody(facts, "Lake Baikal")).toBeNull();
    expect(resolveBody(facts, "Lake Victoria")).toBeNull();
    expect(resolveBody(facts, "the Caspian Sea")).toBeNull();
  });

  it("refuses a generic word rather than guessing", () => {
    expect(resolveBody(facts, "the lake")).toBeNull();
    expect(resolveBody(facts, "water body")).toBeNull();
    expect(resolveBody(facts, "")).toBeNull();
  });

  it("refuses an ambiguous short name instead of picking one", () => {
    // "lake" appears in hundreds of names; a wrong pick would cite the wrong numbers.
    expect(resolveBody(facts, "Lake")).toBeNull();
  });

  it("resolves every catalogued name exactly once", () => {
    for (const body of facts.bodies) {
      expect(resolveBody(facts, body.name)?.id, body.name).toBe(body.id);
    }
  });
});

describe("resolveFrame", () => {
  it("resolves now to the latest observation", () => {
    const id = resolveFrame(facts, "now");
    const frame = facts.dataset.frames.find((entry) => entry.id === id);
    expect(frame?.kind).toBe("observed");
    expect(frame?.date).toBe("2026-09-17");
  });

  it("resolves relative forecast days to the right frames", () => {
    expect(resolveFrame(facts, "+1d")).toBe("fc-01");
    expect(resolveFrame(facts, "7d")).toBe("fc-07");
    expect(resolveFrame(facts, "+7")).toBe("fc-07");
    expect(resolveFrame(facts, "last")).toBe("fc-07");
  });

  it("resolves a frame id and a date", () => {
    expect(resolveFrame(facts, "obs-01")).toBe("obs-01");
    expect(resolveFrame(facts, "2026-09-23")).toBe("fc-07");
  });

  it("refuses a frame that does not exist", () => {
    expect(resolveFrame(facts, "+30d")).toBeNull();
    expect(resolveFrame(facts, "2027-01-01")).toBeNull();
    expect(resolveFrame(facts, "yesterday")).toBeNull();
  });

  it("labels a frame the way the timeline does", () => {
    expect(frameLabel(facts, "fc-07")).toContain("+7d");
    expect(frameLabel(facts, "obs-03")).toContain("2026-09-17");
  });
});

describe("rankBodies", () => {
  it("returns the same order the leaderboard shows", () => {
    const ranking = rankBodies(facts, "worst", 5);
    expect(ranking.total).toBe(250);
    expect(ranking.entries).toHaveLength(5);
    expect(ranking.entries[0].risk).toBe(1);
    for (let index = 1; index < ranking.entries.length; index += 1) {
      expect(ranking.entries[index].risk!).toBeLessThanOrEqual(
        ranking.entries[index - 1].risk!,
      );
    }
    expect(ranking.entries.map((entry) => entry.rank)).toEqual([1, 2, 3, 4, 5]);
  });

  it("reverses for the cleanest and numbers the ranks from the whole set", () => {
    const ranking = rankBodies(facts, "cleanest", 3);
    expect(ranking.entries[0].risk!).toBeLessThanOrEqual(ranking.entries[1].risk!);
    expect(ranking.entries[0].rank).toBe(250);
    expect(ranking.entries[2].rank).toBe(248);
  });

  it("caps the list and flags the bodies the catalogue does not name", () => {
    expect(rankBodies(facts, "worst", 99).entries).toHaveLength(12);
    expect(rankBodies(facts, "worst", 12).entries.some((entry) => !entry.named)).toBe(true);
  });

  it("carries a caution through when the body has one", () => {
    const ranking = rankBodies(facts, "worst", 12);
    const salt = ranking.entries.find((entry) => entry.name === "Great Salt Lake");
    expect(salt?.caution).toMatch(/hypersaline/i);
  });
});

describe("outlookFor", () => {
  it("returns the observed series and the forecast, not prose", () => {
    const body = facts.bodies.find((entry) => entry.id === "lake-okeechobee")!;
    const outlook = outlookFor(body);
    expect(outlook.observed).toHaveLength(4);
    expect(outlook.forecast?.rain_mm).toHaveLength(7);
    expect(outlook.forecast?.runoff_mm).toHaveLength(7);
    expect(outlook.forecast?.rain_mm?.[2]).toBeCloseTo(128.35, 2);
  });

  it("says a body has no forecast rather than inventing one", () => {
    const body = { ...facts.bodies[0], forecast: null };
    expect(outlookFor(body).forecast).toBeNull();
  });
});

describe("CLIENT_ACTIONS", () => {
  it("marks exactly the actions only the browser can perform", () => {
    expect([...CLIENT_ACTIONS].sort()).toEqual(["fly_to", "open_3d", "set_frame"]);
  });
});
