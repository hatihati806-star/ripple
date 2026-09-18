import { decodeRisk, riskRgb } from "../domain/palette";
import { lonLatToMercator, pixelToLonLat, type PixelGrid } from "../map/projection";

/**
 * Relief model for the 3D lake view.
 *
 * The 3D view renders the *same* raster the map shows: the tile's colour is decoded back
 * to the relative risk index and that index becomes height. Colour and height therefore
 * encode one quantity, which is the point -- a bloom pocket is both red and a hill, and
 * nowhere is it prettier (or uglier) than the 2D map says.
 *
 * Everything here is pure array math so it can be tested without a GPU: the three.js layer
 * only turns these arrays into buffers.
 */

/** Below this the crop is too small for a readable relief. */
export const MIN_HALF_KM = 25;
/** Above this a single relief would span more Mercator metres than it gains in detail. */
export const MAX_HALF_KM = 450;
/** The pipeline writes alpha 0 for land and no-data, ~190 for water; below 8 is neither. */
export const WATER_ALPHA = 8;
/** A floor on the crop so a future finer grid cannot produce a 3-pixel lake. */
const MIN_CROP_PX = 24;
/** Slate-400: the same "no observation" grey the map uses, kept as a fallback surface. */
const LAND_RGB: [number, number, number] = [148, 163, 184];

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** Ground half-extent of the crop: the body's equivalent radius, padded and bounded. */
export function halfExtentKm(areaKm2: number | null | undefined, padding = 2.6): number {
  if (areaKm2 == null || !Number.isFinite(areaKm2) || areaKm2 <= 0) return MIN_HALF_KM;
  const radiusKm = Math.sqrt(areaKm2 / Math.PI);
  return clamp(radiusKm * padding, MIN_HALF_KM, MAX_HALF_KM);
}

export interface CropWindow {
  /** Integer pixel rectangle on the display tile, clamped to the raster. */
  pixels: { x: number; y: number; width: number; height: number };
  /** [left, bottom, right, top] in Web Mercator metres, consistent with `pixels`. */
  merc: [number, number, number, number];
  /** [west, south, east, north]. */
  lonLat: [number, number, number, number];
  /** Ground extent of the crop in km; pass these to `buildSurface` for true aspect. */
  extentXKm: number;
  extentZKm: number;
}

/**
 * The pixel rectangle around a lon/lat for a ground half-extent in km.
 *
 * Ground km are converted to Mercator metres with the local 1/cos(lat) scale, so the crop
 * covers the requested ground area at any latitude. The rectangle is then clamped to the
 * raster and grown to `MIN_CROP_PX` if the grid is finer than the body.
 */
export function cropWindow(
  lon: number,
  lat: number,
  halfKm: number,
  grid: PixelGrid,
): CropWindow {
  const [left, bottom, right, top] = grid.mercBounds;
  const [cx, cy] = lonLatToMercator(lon, lat);
  const cosLat = Math.max(0.05, Math.cos((lat * Math.PI) / 180));
  const halfMerc = (halfKm * 1000) / cosLat;

  const toPx = (mx: number) => ((mx - left) / (right - left)) * grid.width;
  const toPy = (my: number) => ((top - my) / (top - bottom)) * grid.height;

  let x0 = Math.floor(clamp(toPx(cx - halfMerc), 0, grid.width - 1));
  let x1 = Math.ceil(clamp(toPx(cx + halfMerc), 1, grid.width));
  let y0 = Math.floor(clamp(toPy(cy + halfMerc), 0, grid.height - 1));
  let y1 = Math.ceil(clamp(toPy(cy - halfMerc), 1, grid.height));

  if (x1 - x0 < MIN_CROP_PX) {
    const grow = Math.ceil((MIN_CROP_PX - (x1 - x0)) / 2);
    x0 = clamp(x0 - grow, 0, Math.max(0, grid.width - MIN_CROP_PX));
    x1 = clamp(x0 + MIN_CROP_PX, MIN_CROP_PX, grid.width);
    x0 = Math.max(0, x1 - MIN_CROP_PX);
  }
  if (y1 - y0 < MIN_CROP_PX) {
    const grow = Math.ceil((MIN_CROP_PX - (y1 - y0)) / 2);
    y0 = clamp(y0 - grow, 0, Math.max(0, grid.height - MIN_CROP_PX));
    y1 = clamp(y0 + MIN_CROP_PX, MIN_CROP_PX, grid.height);
    y0 = Math.max(0, y1 - MIN_CROP_PX);
  }

  // Re-derive the Mercator box from the integer pixels so the two never disagree.
  const mercLeft = left + (x0 / grid.width) * (right - left);
  const mercRight = left + (x1 / grid.width) * (right - left);
  const mercTop = top - (y0 / grid.height) * (top - bottom);
  const mercBottom = top - (y1 / grid.height) * (top - bottom);

  const [west, north] = pixelToLonLat({ x: x0, y: y0 }, grid);
  const [east, south] = pixelToLonLat({ x: x1, y: y1 }, grid);

  return {
    pixels: { x: x0, y: y0, width: x1 - x0, height: y1 - y0 },
    merc: [mercLeft, mercBottom, mercRight, mercTop],
    lonLat: [west, south, east, north],
    extentXKm: ((mercRight - mercLeft) * cosLat) / 1000,
    extentZKm: ((mercTop - mercBottom) * cosLat) / 1000,
  };
}

export interface ReliefGrid {
  width: number;
  height: number;
  /** Relative risk 0..1 per pixel; 0 where `valid` is 0 (the value is meaningless there). */
  risk: Float32Array;
  /** 1 where the tile had a water observation, else 0. */
  valid: Uint8Array;
}

/**
 * Decode a cropped tile's pixels into a risk grid.
 *
 * `decodeRisk` inverts the exact ramp the pipeline rendered, so the value read here is the
 * same index the 2D map shows. Colours repeat heavily across a tile, so results are
 * memoised per unique RGB -- the inversion itself is a 512-entry nearest-colour search.
 */
export function decodeRelief(
  data: Uint8ClampedArray | Uint8Array,
  width: number,
  height: number,
): ReliefGrid {
  const count = Math.max(0, width * height);
  const risk = new Float32Array(count);
  const valid = new Uint8Array(count);
  const cache = new Map<number, number>();

  for (let index = 0, offset = 0; index < count; index++, offset += 4) {
    if (data[offset + 3] < WATER_ALPHA) continue;
    const key = (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2];
    let value = cache.get(key);
    if (value === undefined) {
      value = decodeRisk(data[offset], data[offset + 1], data[offset + 2]);
      cache.set(key, value);
    }
    risk[index] = value;
    valid[index] = 1;
  }
  return { width, height, risk, valid };
}

/**
 * Centre-weighted 3x3 smoothing over valid pixels, for the 3D surface only.
 *
 * One 1.85 km pixel is noisier than the feature it belongs to, and at relief scale that
 * noise becomes a pincushion of one-cell spikes. This is the same reasoning the point
 * sampler uses when it averages a 3x3 neighbourhood -- the difference is that here the
 * *displayed* value is the average, so the HUD must (and does) say so. Invalid pixels stay
 * invalid: smoothing never invents an observation.
 */
export function smoothRelief(relief: ReliefGrid): ReliefGrid {
  const { width, height, risk, valid } = relief;
  const out = new Float32Array(risk.length);
  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) {
      const index = row * width + col;
      if (valid[index] !== 1) continue;
      let sum = 0;
      let weight = 0;
      for (let dy = -1; dy <= 1; dy++) {
        const y = row + dy;
        if (y < 0 || y >= height) continue;
        for (let dx = -1; dx <= 1; dx++) {
          const x = col + dx;
          if (x < 0 || x >= width) continue;
          const neighbour = y * width + x;
          if (valid[neighbour] !== 1) continue;
          const w = dx === 0 && dy === 0 ? 2 : 1;
          sum += risk[neighbour] * w;
          weight += w;
        }
      }
      out[index] = weight > 0 ? sum / weight : risk[index];
    }
  }
  return { width, height, risk: out, valid };
}

export interface ReliefSurface {
  /** xyz vertex positions in km; x east, y up (risk), z south. */
  positions: Float32Array;
  /** Linear-space rgb per vertex, matching the map ramp; grey where there is no water. */
  colors: Float32Array;
  indices: Uint32Array;
  vertexCount: number;
  quadCount: number;
  peak: { col: number; row: number; risk: number } | null;
  meanRisk: number | null;
  waterFraction: number;
}

function srgbToLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/**
 * Turn a risk grid into an indexed surface.
 *
 * A quad needs *two* corners with water: one is usually a stray mixed shoreline pixel, and
 * rendering those as single-cell spikes turns a lake into a pincushion. Corners without an
 * observation keep height 0 and the no-observation grey, which is exactly the map's
 * convention: grey means the satellite saw nothing there, not that the water is clean.
 */
export function buildSurface(
  relief: ReliefGrid,
  extentXKm: number,
  extentZKm: number,
  heightKm: number,
): ReliefSurface {
  const { width, height, risk, valid } = relief;
  const vertexCount = Math.max(0, width * height);
  const positions = new Float32Array(vertexCount * 3);
  const colors = new Float32Array(vertexCount * 3);
  const safeHeight = Number.isFinite(heightKm) ? heightKm : 0;

  const spanX = width > 1 ? extentXKm : 0;
  const spanZ = height > 1 ? extentZKm : 0;
  const landLinear = LAND_RGB.map(srgbToLinear) as [number, number, number];

  let validCount = 0;
  let riskSum = 0;
  let peak: ReliefSurface["peak"] = null;

  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) {
      const index = row * width + col;
      const offset = index * 3;
      const isWater = valid[index] === 1;
      const value = isWater ? risk[index] : 0;

      positions[offset] = width > 1 ? (col / (width - 1) - 0.5) * spanX : 0;
      positions[offset + 1] = isWater ? value * safeHeight : 0;
      positions[offset + 2] = height > 1 ? (row / (height - 1) - 0.5) * spanZ : 0;

      if (isWater) {
        const [r, g, b] = riskRgb(value);
        colors[offset] = srgbToLinear(r);
        colors[offset + 1] = srgbToLinear(g);
        colors[offset + 2] = srgbToLinear(b);
        validCount += 1;
        riskSum += value;
        if (!peak || value > peak.risk) peak = { col, row, risk: value };
      } else {
        colors[offset] = landLinear[0];
        colors[offset + 1] = landLinear[1];
        colors[offset + 2] = landLinear[2];
      }
    }
  }

  const indices: number[] = [];
  let quadCount = 0;
  for (let row = 0; row < height - 1; row++) {
    for (let col = 0; col < width - 1; col++) {
      const a = row * width + col;
      const b = a + 1;
      const c = a + width;
      const d = c + 1;
      const corners = valid[a] + valid[b] + valid[c] + valid[d];
      if (corners < 2) continue;
      indices.push(a, c, b, b, c, d);
      quadCount += 1;
    }
  }

  return {
    positions,
    colors,
    indices: Uint32Array.from(indices),
    vertexCount,
    quadCount,
    peak,
    meanRisk: validCount > 0 ? riskSum / validCount : null,
    waterFraction: vertexCount > 0 ? validCount / vertexCount : 0,
  };
}
