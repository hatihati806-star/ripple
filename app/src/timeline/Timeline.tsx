import { useEffect, useRef } from "react";
import type { Frame } from "../domain/types";
import type { PreloadProgress } from "../data/frames";

interface TimelineProps {
  frames: Frame[];
  index: number;
  playing: boolean;
  speed: number;
  onIndex: (index: number) => void;
  onToggle: () => void;
  onSpeed: (speed: number) => void;
  preload: PreloadProgress;
}

function isNowBoundary(frames: Frame[], index: number): boolean {
  return (
    frames[index]?.kind === "observed" &&
    frames[index + 1]?.kind === "forecast"
  );
}

/**
 * Filmstrip timeline: observed composites on the left, forecast days on the right, with a
 * visible "now" divider between them.
 *
 * Chips rather than a bare range input, because the frames are discrete real dates and the
 * user needs to know *which* date they are looking at, not just where they are on a track.
 */
export function Timeline({
  frames,
  index,
  playing,
  speed,
  onIndex,
  onToggle,
  onSpeed,
  preload,
}: TimelineProps) {
  const strip = useRef<HTMLDivElement>(null);
  const active = frames[index];
  const forecastCount = frames.filter((frame) => frame.kind === "forecast").length;
  const observedCount = frames.length - forecastCount;
  const isForecast = active?.kind === "forecast";

  useEffect(() => {
    const container = strip.current;
    if (!container) return;
    const chip = container.querySelector<HTMLElement>(`[data-chip="${index}"]`);
    chip?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }, [index]);

  const preloadPercent =
    preload.total === 0 ? 100 : Math.round((preload.loaded / preload.total) * 100);
  const preloading = preload.loaded < preload.total;

  return (
    <div className="rounded-xl bg-white/92 px-3 py-2 shadow-lg backdrop-blur">
      <div className="mb-1.5 flex items-center gap-3">
        <button
          type="button"
          onClick={onToggle}
          aria-label={playing ? "Pause radar loop" : "Play radar loop"}
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-white transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 ${
            playing ? "bg-slate-700" : "bg-slate-900 hover:bg-slate-700"
          }`}
        >
          <span aria-hidden="true" className="text-sm leading-none">
            {playing ? "❚❚" : "▶"}
          </span>
        </button>

        <div className="min-w-0 flex-1">
          <div
            className={`truncate text-sm font-semibold ${
              isForecast ? "text-orange-600" : "text-slate-800"
            }`}
          >
            {active?.kind === "forecast"
              ? `Forecast · ${active.window_label}`
              : active?.latest
                ? "Now · latest observed composite"
                : `Observed · ${active?.window_label ?? ""}`}
          </div>
          <div className="flex items-center gap-1.5 truncate text-[10px] text-slate-500">
            <span>
              {index + 1} / {frames.length} · {observedCount} observed
              {forecastCount > 0 ? ` + ${forecastCount} forecast` : ""}
            </span>
            {preloading && (
              <span
                className="inline-flex items-center gap-1 text-slate-400"
                title={`Preloading frames: ${preloadPercent}%`}
              >
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />
                buffering {preloadPercent}%
              </span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {[1, 2].map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => onSpeed(option)}
              aria-pressed={speed === option}
              aria-label={`Playback speed ${option}x`}
              className={`h-11 w-11 rounded-lg text-[11px] font-semibold transition ${
                speed === option
                  ? "bg-slate-900 text-white"
                  : "bg-slate-100 text-slate-500 hover:text-slate-700"
              }`}
            >
              {option}×
            </button>
          ))}
        </div>
      </div>

      <div
        ref={strip}
        className="flex items-stretch gap-1 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        role="tablist"
        aria-label="Timeline frames"
      >
        {frames.map((frame, frameIndex) => {
          const selected = frameIndex === index;
          const forecast = frame.kind === "forecast";
          return (
            <div key={frame.id} className="flex items-stretch">
              <button
                type="button"
                role="tab"
                data-chip={frameIndex}
                aria-selected={selected}
                aria-label={
                  forecast
                    ? `Forecast ${frame.label}`
                    : `Observed composite ${frame.window_label}`
                }
                onClick={() => onIndex(frameIndex)}
                className={`flex min-h-11 min-w-[52px] flex-col items-center justify-center rounded-lg px-2 py-1 text-center transition ${
                  selected
                    ? forecast
                      ? "bg-orange-500 text-white shadow-sm"
                      : "bg-slate-900 text-white shadow-sm"
                    : forecast
                      ? "bg-orange-50 text-orange-700 hover:bg-orange-100"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                <span className="text-[11px] font-semibold leading-tight tabular-nums">
                  {frame.label}
                </span>
                <span
                  className={`text-[9px] leading-tight ${
                    selected ? "text-white/70" : "text-slate-400"
                  }`}
                >
                  {forecast ? "fcst" : frame.latest ? "now" : "obs"}
                </span>
              </button>
              {isNowBoundary(frames, frameIndex) && (
                <div
                  className="mx-1 flex flex-col items-center justify-center"
                  aria-hidden="true"
                >
                  <span className="h-4 w-px bg-slate-300" />
                  <span className="text-[8px] font-semibold tracking-wide text-slate-400">
                    NOW
                  </span>
                  <span className="h-4 w-px bg-orange-300" />
                </div>
              )}
            </div>
          );
        })}
        {frames.length === 0 && (
          <span className="py-2 text-[11px] text-slate-500">No frames available.</span>
        )}
      </div>

      <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-slate-900 transition-[width] duration-200 ease-out"
          style={{ width: `${Math.round((index / Math.max(1, frames.length - 1)) * 100)}%` }}
        />
      </div>
    </div>
  );
}
