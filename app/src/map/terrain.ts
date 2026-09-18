/**
 * Terrain for the 3D map mode.
 *
 * The DEM is AWS's mirror of the Mapzen terrain tiles: key-free, global, and
 * terrarium-encoded (elevation = (R * 256 + G + B / 256) - 32768), which is exactly what
 * MapLibre's `raster-dem` source expects. It keeps Ripple's whole data stack key-free.
 *
 * Nothing here is fetched until the user first turns 3D on: a raster-dem source on a flat
 * map would be dead weight, and the tiles are only requested once `setTerrain` uses them.
 */
export const TERRAIN_SOURCE_ID = "ripple-terrain";

export const TERRAIN_TILE_TEMPLATE =
  "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

export const TERRAIN_ATTRIBUTION =
  'Terrain: <a href="https://registry.opendata.aws/terrain-tiles/">AWS Terrain Tiles</a> (Mapzen)';

export const TERRAIN_ENCODING = "terrarium";

/** Exaggeration: true relief is nearly flat at continental zoom; this is honest in the UI. */
export const TERRAIN_EXAGGERATION = 1.35;

/** Camera pose for the 3D map mode. */
export const MAP3D_PITCH = 55;
export const MAP3D_BEARING = -15;

export interface TerrainSpec {
  source: string;
  exaggeration: number;
}

/** The argument `map.setTerrain` takes when 3D is on. */
export function terrainSpec(): TerrainSpec {
  return { source: TERRAIN_SOURCE_ID, exaggeration: TERRAIN_EXAGGERATION };
}

/** Expand the tile template for a z/x/y, for prefetching or diagnostics. */
export function terrainTileUrl(z: number, x: number, y: number): string {
  return TERRAIN_TILE_TEMPLATE.replace("{z}", String(z))
    .replace("{x}", String(x))
    .replace("{y}", String(y));
}
