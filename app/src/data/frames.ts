import type { Frame } from "../domain/types";

/**
 * Frame preloading.
 *
 * A radar loop is only as smooth as its coldest frame: the moment a source swaps to an
 * uncached tile, MapLibre has nothing to draw and the canvas flashes. Every frame is
 * therefore fetched and decoded ahead of time, in the background, at low concurrency so
 * playback never competes with the frames the user is looking at.
 */

export interface PreloadProgress {
  loaded: number;
  total: number;
}

const cache = new Map<string, HTMLImageElement>();

export function cachedImage(url: string): HTMLImageElement | null {
  return cache.get(url) ?? null;
}

/** Fetch and decode one image; resolves to the element (also cached for MapLibre). */
export async function preloadImage(url: string): Promise<HTMLImageElement | null> {
  const existing = cache.get(url);
  if (existing?.complete) return existing;

  const image = new Image();
  image.decoding = "async";
  image.src = url;
  try {
    await image.decode();
  } catch {
    return null;
  }
  cache.set(url, image);
  return image;
}

/**
 * Preload frames in the given order, `concurrency` at a time.
 * The current frame and its neighbours should be passed first.
 */
export async function preloadFrames(
  frames: Frame[],
  baseUrl = "data",
  concurrency = 3,
  onProgress?: (progress: PreloadProgress) => void,
): Promise<void> {
  const queue = [...frames];
  const total = queue.length;
  let loaded = 0;

  async function worker() {
    while (queue.length > 0) {
      const frame = queue.shift();
      if (!frame) return;
      await preloadImage(`${baseUrl}/${frame.tile}`);
      loaded += 1;
      onProgress?.({ loaded, total });
    }
  }

  await Promise.all(Array.from({ length: Math.min(concurrency, total) }, worker));
}

/** Frames ordered outwards from `index`: current, next, previous, next+1, ... */
export function preloadOrder(frames: Frame[], index: number): Frame[] {
  const order: Frame[] = [];
  for (let offset = 0; offset < frames.length; offset++) {
    const ahead = index + offset;
    const behind = index - offset;
    if (offset === 0) {
      if (frames[index]) order.push(frames[index]);
      continue;
    }
    if (ahead < frames.length) order.push(frames[ahead]);
    if (behind >= 0) order.push(frames[behind]);
  }
  return order;
}
