import type { PixelGrid } from "../map/projection";
import {
  buildSurface,
  cropWindow,
  decodeRelief,
  halfExtentKm,
  smoothRelief,
  type CropWindow,
  type ReliefGrid,
  type ReliefSurface,
} from "./relief";

/** Long edge of the relief grid. 200 x 200 keeps playback smooth on integrated GPUs. */
export const GRID_MAX = 200;

/** Risk 1.0 rises this fraction of the shorter ground extent before exaggeration. */
const HEIGHT_FRACTION = 0.035;

export interface SurfaceData {
  crop: CropWindow;
  relief: ReliefGrid;
  surface: ReliefSurface;
  /** km of height for a risk of 1.0. */
  heightKm: number;
}

/**
 * Build the relief for one body from one display tile.
 *
 * The tile is decoded once, and only the crop around the body is drawn down into a
 * <= 200 px canvas -- getImageData on a full 4096 px tile would be 67 MB of bitmap per
 * frame for a window the user never sees. The crop is never upscaled: a small lake gets a
 * small grid at native detail rather than a blurry big one.
 */
export async function loadSurface(
  tileUrl: string,
  grid: PixelGrid,
  body: { lon: number; lat: number; area_km2?: number | null },
  signal?: AbortSignal,
): Promise<SurfaceData> {
  const response = await fetch(tileUrl, { signal });
  if (!response.ok) throw new Error(`tile request failed: ${response.status}`);
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob, { premultiplyAlpha: "none" });
  try {
    const crop = cropWindow(body.lon, body.lat, halfExtentKm(body.area_km2), grid);
    const scale = Math.min(
      1,
      GRID_MAX / Math.max(crop.pixels.width, crop.pixels.height),
    );
    const width = Math.max(2, Math.round(crop.pixels.width * scale));
    const height = Math.max(2, Math.round(crop.pixels.height * scale));

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("no 2d context for the relief crop");
    context.imageSmoothingEnabled = true;
    context.drawImage(
      bitmap,
      crop.pixels.x,
      crop.pixels.y,
      crop.pixels.width,
      crop.pixels.height,
      0,
      0,
      width,
      height,
    );
    const image = context.getImageData(0, 0, width, height);

    const relief = smoothRelief(decodeRelief(image.data, width, height));
    const heightKm = HEIGHT_FRACTION * Math.min(crop.extentXKm, crop.extentZKm);
    return {
      crop,
      relief,
      heightKm,
      surface: buildSurface(relief, crop.extentXKm, crop.extentZKm, heightKm),
    };
  } finally {
    bitmap.close();
  }
}
