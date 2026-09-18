import { describe, expect, it } from "vitest";
import {
  MAP3D_PITCH,
  TERRAIN_ENCODING,
  TERRAIN_SOURCE_ID,
  terrainSpec,
  terrainTileUrl,
} from "./terrain";

describe("terrain 3D mode", () => {
  it("expands the tile template without leaving placeholders", () => {
    const url = terrainTileUrl(5, 8, 12);
    expect(url).toContain("/5/8/12.png");
    expect(url).not.toContain("{");
  });

  it("uses terrarium encoding, which is what the AWS tiles are", () => {
    expect(TERRAIN_ENCODING).toBe("terrarium");
  });

  it("points the terrain spec at the declared source with a sane exaggeration", () => {
    const spec = terrainSpec();
    expect(spec.source).toBe(TERRAIN_SOURCE_ID);
    expect(spec.exaggeration).toBeGreaterThan(1);
    expect(spec.exaggeration).toBeLessThan(3);
  });

  it("pitches the camera up but below the horizon-breaking limit", () => {
    expect(MAP3D_PITCH).toBeGreaterThan(20);
    expect(MAP3D_PITCH).toBeLessThan(70);
  });
});
