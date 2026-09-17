import { describe, expect, it } from "vitest";
import { preloadOrder } from "./frames";
import type { Frame } from "../domain/types";

function frame(id: string): Frame {
  return {
    id,
    kind: "observed",
    label: id,
    window_label: id,
    date: "2026-09-16",
    tile: `${id}.png`,
    probe_tile: `${id}-probe.png`,
    latest: false,
    age_days: 1,
  };
}

describe("preloadOrder", () => {
  const frames = ["a", "b", "c", "d", "e"].map(frame);

  it("starts with the frame on screen", () => {
    expect(preloadOrder(frames, 2)[0].id).toBe("c");
  });

  it("alternates forward and backward from the playhead", () => {
    expect(preloadOrder(frames, 2).map((entry) => entry.id)).toEqual([
      "c",
      "d",
      "b",
      "e",
      "a",
    ]);
  });

  it("handles the first and last positions without dropping frames", () => {
    expect(preloadOrder(frames, 0).map((entry) => entry.id)).toEqual(["a", "b", "c", "d", "e"]);
    expect(preloadOrder(frames, 4).map((entry) => entry.id)).toEqual(["e", "d", "c", "b", "a"]);
  });

  it("returns an empty order for an empty timeline", () => {
    expect(preloadOrder([], 0)).toEqual([]);
  });
});
