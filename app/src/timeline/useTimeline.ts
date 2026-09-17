import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Frame } from "../domain/types";

export const BASE_TICK_MS = 900;
export const SPEEDS = [1, 2] as const;
export type Speed = (typeof SPEEDS)[number];

/**
 * Timeline state for the radar loop.
 *
 * Opens at the boundary between observation and forecast -- "now" -- so the first thing
 * shown is the most recent real measurement rather than an extrapolation. Stepping clamps
 * at both ends (a scrubber that silently wraps is disorienting), while *playback* loops:
 * that is what makes it read as a radar animation rather than a slideshow that stops.
 */
export function useTimeline(frames: Frame[]) {
  const lastObserved = useMemo(() => {
    const index = frames.map((f) => f.kind).lastIndexOf("observed");
    return index === -1 ? Math.max(0, frames.length - 1) : index;
  }, [frames]);

  const [index, setRawIndex] = useState(lastObserved);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedRaw] = useState<Speed>(1);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    setRawIndex(lastObserved);
  }, [lastObserved]);

  const setIndex = useCallback(
    (next: number) =>
      setRawIndex(Math.min(Math.max(0, next), Math.max(0, frames.length - 1))),
    [frames.length],
  );

  const next = useCallback(
    () => setRawIndex((i) => Math.min(i + 1, Math.max(0, frames.length - 1))),
    [frames.length],
  );

  const prev = useCallback(() => setRawIndex((i) => Math.max(0, i - 1)), []);

  const setSpeed = useCallback((value: number) => {
    setSpeedRaw(value >= 2 ? 2 : 1);
  }, []);

  useEffect(() => {
    if (!playing || frames.length === 0) return;
    timer.current = window.setInterval(() => {
      setRawIndex((i) => (i + 1) % Math.max(1, frames.length));
    }, BASE_TICK_MS / speed);
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [playing, frames.length, speed]);

  const toggle = useCallback(() => {
    setPlaying((wasPlaying) => {
      if (!wasPlaying) setRawIndex((i) => (i >= frames.length - 1 ? 0 : i));
      return !wasPlaying;
    });
  }, [frames.length]);

  const pause = useCallback(() => setPlaying(false), []);

  return {
    index,
    frame: frames[index] ?? null,
    setIndex,
    next,
    prev,
    toggle,
    pause,
    playing,
    speed,
    setSpeed,
    progress: frames.length <= 1 ? 0 : index / (frames.length - 1),
  };
}
