import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useTimeline } from "./useTimeline";
import type { Frame } from "../domain/types";

const frames: Frame[] = [0, 1, 2, 3].map((i) => ({
  id: `f${i}`,
  kind: i < 2 ? "observed" : "forecast",
  label: `t${i}`,
  window_label: `t${i}`,
  date: "2026-09-16",
  tile: `t${i}.png`,
  probe_tile: `t${i}-probe.png`,
  latest: i === 1,
  age_days: i,
}));

describe("useTimeline", () => {
  it("opens at the last observed frame, the now boundary", () => {
    const { result } = renderHook(() => useTimeline(frames));
    expect(result.current.index).toBe(1);
    expect(result.current.frame?.id).toBe("f1");
  });

  it("clamps stepping at both ends", () => {
    const { result } = renderHook(() => useTimeline(frames));
    act(() => result.current.setIndex(-5));
    expect(result.current.index).toBe(0);
    act(() => result.current.setIndex(99));
    expect(result.current.index).toBe(3);
  });

  it("stops at the end rather than wrapping", () => {
    const { result } = renderHook(() => useTimeline(frames));
    act(() => result.current.setIndex(3));
    act(() => result.current.next());
    expect(result.current.index).toBe(3);
  });

  it("steps backward and forward", () => {
    const { result } = renderHook(() => useTimeline(frames));
    act(() => result.current.prev());
    expect(result.current.index).toBe(0);
    act(() => result.current.next());
    expect(result.current.index).toBe(1);
  });

  it("handles an empty frame list without throwing", () => {
    const { result } = renderHook(() => useTimeline([]));
    expect(result.current.frame).toBeNull();
    expect(result.current.index).toBe(0);
    expect(result.current.progress).toBe(0);
  });

  it("opens at forecast-only data without a negative index", () => {
    const forecastOnly = frames.filter((f) => f.kind === "forecast");
    const { result } = renderHook(() => useTimeline(forecastOnly));
    expect(result.current.index).toBe(forecastOnly.length - 1);
  });

  it("reports progress across the timeline", () => {
    const { result } = renderHook(() => useTimeline(frames));
    act(() => result.current.setIndex(3));
    expect(result.current.progress).toBeCloseTo(1);
  });

  it("rejects a timeline of one frame as non-progressing", () => {
    const { result } = renderHook(() => useTimeline([frames[0]]));
    expect(result.current.progress).toBe(0);
    act(() => result.current.next());
    expect(result.current.index).toBe(0);
  });

  it("loops playback past the end instead of stopping", () => {
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useTimeline(frames));
      act(() => result.current.setIndex(2));
      act(() => result.current.toggle());
      expect(result.current.playing).toBe(true);
      act(() => {
        vi.advanceTimersByTime(900);
      });
      expect(result.current.index).toBe(3);
      act(() => {
        vi.advanceTimersByTime(900);
      });
      expect(result.current.index).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("restarts from the first frame when play begins at the end", () => {
    const { result } = renderHook(() => useTimeline(frames));
    act(() => result.current.setIndex(3));
    act(() => result.current.toggle());
    expect(result.current.index).toBe(0);
  });

  it("advances twice as fast at 2x", () => {
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useTimeline(frames));
      act(() => result.current.setIndex(0));
      act(() => result.current.setSpeed(2));
      act(() => result.current.toggle());
      act(() => {
        vi.advanceTimersByTime(450);
      });
      expect(result.current.index).toBe(1);
      act(() => {
        vi.advanceTimersByTime(450);
      });
      expect(result.current.index).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("clamps the speed to the supported values", () => {
    const { result } = renderHook(() => useTimeline(frames));
    act(() => result.current.setSpeed(99));
    expect(result.current.speed).toBe(2);
    act(() => result.current.setSpeed(0));
    expect(result.current.speed).toBe(1);
  });
});
