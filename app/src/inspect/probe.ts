import type { Frame } from "../domain/types";
import { decodeRisk } from "../domain/palette";
import { lonLatToPixel, type PixelGrid } from "../map/projection";

/**
 * Point inspection reads the *probe* tiles rather than the display tiles.
 *
 * A display tile is ~750 KB at 4096 px; decoding one per frame to sample 9 pixels would
 * cost tens of megabytes of bitmap per frame. The pipeline therefore emits a decimated
 * sibling (long edge <= 1024) with the same colours, which is what a browser can hold for
 * a dozen frames at once. The cost is spatial: a probe pixel is metres*stride on the
 * ground, reported to the user as the sample footprint.
 */

export interface ProbeResult {
  /** Ramp value 0..1 per frame, or null where the pixel is not valid water. */
  risk: (number | null)[];
  /** True when the sampled neighbourhood had any valid pixel. */
  valid: boolean;
}

export interface ProbeCanvas {
  width: number;
  height: number;
  data: Uint8ClampedArray;
}

/** Draw a decoded bitmap into a canvas and return its pixels. */
export function canvasFromBitmap(bitmap: ImageBitmap): ProbeCanvas | null {
  if (typeof OffscreenCanvas !== "undefined") {
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.drawImage(bitmap, 0, 0);
    const image = context.getImageData(0, 0, bitmap.width, bitmap.height);
    return { width: bitmap.width, height: bitmap.height, data: image.data };
  }
  return null;
}

async function decode(url: string, maxDim: number): Promise<ProbeCanvas | null> {
  const response = await fetch(url);
  if (!response.ok) return null;
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob, {
    resizeWidth: maxDim,
    resizeQuality: "pixelated",
    premultiplyAlpha: "none",
  });
  try {
    return canvasFromBitmap(bitmap);
  } finally {
    bitmap.close();
  }
}

/**
 * Load every frame's probe tile at a bounded size, reporting progress as it goes.
 * Returns an array aligned with `frames`; failed frames are null.
 *
 * `baseUrl` must match how the display tiles are addressed ("data"), because `probe_tile`
 * in the manifest is a path relative to that directory -- fetching it verbatim resolves
 * against the site root and 404s every frame.
 */
export async function loadProbeCanvases(
  frames: Frame[],
  maxDim = 1024,
  onProgress?: (done: number, total: number) => void,
  baseUrl = "data",
): Promise<(ProbeCanvas | null)[]> {
  const out: (ProbeCanvas | null)[] = new Array(frames.length).fill(null);
  let done = 0;
  const concurrency = 4;
  let cursor = 0;

  async function worker() {
    while (cursor < frames.length) {
      const index = cursor++;
      try {
        out[index] = await decode(`${baseUrl}/${frames[index].probe_tile}`, maxDim);
      } catch {
        out[index] = null;
      }
      done += 1;
      onProgress?.(done, frames.length);
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, frames.length) }, worker),
  );
  return out;
}

/**
 * Sample one location across all probe canvases.
 *
 * A 3x3 neighbourhood is read and valid pixels are averaged: the grid is finer than the
 * screen, so a single pixel is noisier than the thing the user is pointing at.
 */
export function sampleProbes(
  canvases: (ProbeCanvas | null)[],
  grid: PixelGrid,
  lon: number,
  lat: number,
  neighbourhood = 1,
): ProbeResult {
  const pixel = lonLatToPixel(lon, lat, grid);
  if (!pixel) return { risk: [], valid: false };

  const cx = Math.floor(pixel.x);
  const cy = Math.floor(pixel.y);
  const risks: (number | null)[] = [];
  let anyValid = false;

  for (const canvas of canvases) {
    if (!canvas) {
      risks.push(null);
      continue;
    }
    let sum = 0;
    let count = 0;
    for (let dy = -neighbourhood; dy <= neighbourhood; dy++) {
      for (let dx = -neighbourhood; dx <= neighbourhood; dx++) {
        const x = cx + dx;
        const y = cy + dy;
        if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) continue;
        const offset = (y * canvas.width + x) * 4;
        // The pipeline writes alpha 0 for "no data / not water" and ~190 for valid.
        if (canvas.data[offset + 3] < 8) continue;
        sum += decodeRisk(
          canvas.data[offset],
          canvas.data[offset + 1],
          canvas.data[offset + 2],
        );
        count += 1;
      }
    }
    if (count === 0) {
      risks.push(null);
    } else {
      risks.push(sum / count);
      anyValid = true;
    }
  }
  return { risk: risks, valid: anyValid };
}
