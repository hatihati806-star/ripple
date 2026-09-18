const EARTH_RADIUS_M = 6378137;
const MAX_LATITUDE = 85.05112878;

/** Longitude/latitude to Web Mercator metres (EPSG:3857), clamped to the projection limit. */
export function lonLatToMercator(lon: number, lat: number): [number, number] {
  const clampedLat = Math.min(MAX_LATITUDE, Math.max(-MAX_LATITUDE, lat));
  const x = (lon * Math.PI * EARTH_RADIUS_M) / 180;
  const y =
    (Math.log(Math.tan(Math.PI / 4 + (clampedLat * Math.PI) / 360)) * EARTH_RADIUS_M);
  return [x, y];
}

export interface PixelGrid {
  width: number;
  height: number;
  /** [left, bottom, right, top] in Web Mercator metres. */
  mercBounds: [number, number, number, number];
}

export interface Pixel {
  x: number;
  y: number;
}

/**
 * Longitude/latitude to pixel coordinates on a grid that covers `mercBounds`.
 *
 * The pipeline builds its grid in Web Mercator, so this is exact rather than
 * approximate: it is the same transform the raster was written in.
 */
export function lonLatToPixel(
  lon: number,
  lat: number,
  grid: PixelGrid,
): Pixel | null {
  if (grid.width <= 0 || grid.height <= 0) return null;
  const [left, bottom, right, top] = grid.mercBounds;
  if (right <= left || top <= bottom) return null;

  const [x, y] = lonLatToMercator(lon, lat);
  const px = ((x - left) / (right - left)) * grid.width;
  const py = ((top - y) / (top - bottom)) * grid.height;
  if (px < 0 || py < 0 || px >= grid.width || py >= grid.height) return null;
  return { x: px, y: py };
}

/** Inverse of {@link lonLatToPixel}. */
export function pixelToLonLat(pixel: Pixel, grid: PixelGrid): [number, number] {
  const [left, bottom, right, top] = grid.mercBounds;
  const x = left + (pixel.x / grid.width) * (right - left);
  const y = top - (pixel.y / grid.height) * (top - bottom);
  const lon = (x / EARTH_RADIUS_M) * (180 / Math.PI);
  const lat =
    (Math.atan(Math.sinh(y / EARTH_RADIUS_M)) * 180) / Math.PI;
  return [lon, lat];
}

/**
 * The display tile's grid: its dimensions from the manifest and its Mercator bounds,
 * falling back to the region bbox when the manifest predates `grid.merc_bounds`.
 */
export function mercGridOf(manifest: {
  grid?: { width: number; height: number; merc_bounds?: [number, number, number, number] };
  region_bbox: [number, number, number, number];
}): PixelGrid | null {
  const width = manifest.grid?.width ?? 0;
  const height = manifest.grid?.height ?? 0;
  if (width <= 0 || height <= 0) return null;
  let mercBounds = manifest.grid?.merc_bounds;
  if (!mercBounds) {
    const [west, south, east, north] = manifest.region_bbox;
    const [x0, y0] = lonLatToMercator(west, south);
    const [x1, y1] = lonLatToMercator(east, north);
    mercBounds = [x0, y0, x1, y1];
  }
  return { width, height, mercBounds };
}

/** Ground distance in km between two lon/lat points (equirectangular, fine at this scale). */
export function distanceKm(
  aLon: number,
  aLat: number,
  bLon: number,
  bLat: number,
): number {
  const meanLat = ((aLat + bLat) / 2) * (Math.PI / 180);
  const dx = (bLon - aLon) * Math.cos(meanLat) * 111.32;
  const dy = (bLat - aLat) * 110.57;
  return Math.hypot(dx, dy);
}
