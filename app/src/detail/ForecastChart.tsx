import { useMemo, useState } from "react";
import {
  areaPath,
  continuousRuns,
  nearestIndex,
  polylinePath,
  rainBarHeights,
  riskSeriesToPoints,
  type ChartBox,
} from "./chartGeometry";
import { riskBand, riskColor } from "../domain/palette";
import { formatDate } from "../water/ranking";

export interface ChartPointInput {
  key: string;
  /** Axis label, e.g. "Sep 18" or "+3d". */
  label: string;
  date: string;
  risk: number | null;
  kind: "observed" | "forecast";
  rain?: number | null;
}

interface ForecastChartProps {
  points: ChartPointInput[];
  /** Index of the last observed point; -1 when the series is forecast-only. */
  boundary: number;
  emptyHint?: string;
}

const BOX: ChartBox = { width: 320, height: 132, paddingTop: 14, paddingBottom: 24 };
const RAIN_MAX_PX = 44;

/**
 * Observed history plus runoff-driven forecast.
 *
 * The risk axis is pinned to the index's own 0..1 range rather than autoscaled: the value
 * is already a position within the region's observed range, and autoscaling would make a
 * nearly-clean lake look as dramatic as an intense bloom. Rain is drawn as bars behind the
 * line because rain is the *driver* of the forecast, not another water-quality signal.
 */
export function ForecastChart({ points, boundary, emptyHint }: ForecastChartProps) {
  const [hover, setHover] = useState<number | null>(null);

  const series = useMemo(
    () => points.filter((point) => point.risk != null || point.kind === "forecast"),
    [points],
  );
  const values = useMemo(
    () => series.map((point) => point.risk ?? 0),
    [series],
  );
  const projected = useMemo(() => riskSeriesToPoints(values, BOX), [values]);
  const rain = useMemo(
    () => series.map((point) => point.rain ?? 0),
    [series],
  );
  const bars = useMemo(() => rainBarHeights(rain, RAIN_MAX_PX), [rain]);

  const { observedRuns, forecastRun } = useMemo(() => {
    const hasValue = series.map((point) => point.risk != null);
    const isForecast = series.map((point) => point.kind === "forecast");
    const observedRuns = continuousRuns(
      projected,
      hasValue.map((has, index) => has && !isForecast[index]),
    );
    const forecastRuns = continuousRuns(
      projected,
      hasValue.map((has, index) => has && isForecast[index]),
    );
    // The dashed forecast is anchored to the last real observation, so it starts there
    // rather than floating one sample to the right.
    const lastObserved = observedRuns[observedRuns.length - 1];
    if (lastObserved && forecastRuns.length > 0) {
      const anchor = lastObserved[lastObserved.length - 1];
      if (anchor.index + 1 === forecastRuns[0][0].index) {
        forecastRuns[0] = [anchor, ...forecastRuns[0]];
      }
    }
    return { observedRuns, forecastRun: forecastRuns.flat() };
  }, [projected, series]);

  if (series.length === 0 || series.every((point) => point.risk == null)) {
    return (
      <p className="text-[11px] leading-snug text-slate-500">
        {emptyHint ?? "No series available for this location yet."}
      </p>
    );
  }

  const baselineY = BOX.height - BOX.paddingBottom;
  const observedPoints = observedRuns.flat();
  const lastObserved = observedPoints[observedPoints.length - 1];
  /** The "now" reading: the newest observed frame that actually has a value. */
  const nowIndex = lastObserved?.index ?? boundary;
  const forecastValues = series
    .filter((point) => point.kind === "forecast" && point.risk != null)
    .map((point) => point.risk as number);
  const peak = Math.max(...values, 0);
  const latestWithValue = [...series].reverse().find((point) => point.risk != null);
  const band = riskBand(latestWithValue?.risk ?? null);
  const accent = band?.color ?? "#0F172A";
  const hasForecast = forecastRun.length > 1;
  /** Middle axis label marks "now", not the last forecast day. */
  const nowDate = series[nowIndex]?.date;

  const active = hover != null ? series[hover] : null;
  const activePoint = hover != null ? projected[hover] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${BOX.width} ${BOX.height}`}
        className="w-full"
        style={{ aspectRatio: `${BOX.width} / ${BOX.height}` }}
        role="img"
        aria-label={
          `Water-quality index: ${observedPoints.length} observed frames` +
          (forecastValues.length > 0 ? `, ${forecastValues.length} forecast days` : "")
        }
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const x = ((event.clientX - rect.left) / rect.width) * BOX.width;
          setHover(nearestIndex(x, BOX, series.length));
        }}
      >
        <defs>
          <linearGradient id="ripple-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={accent} stopOpacity="0.34" />
            <stop offset="100%" stopColor={accent} stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {[0.25, 0.5, 0.75].map((tick) => {
          const y = BOX.paddingTop + (1 - tick) * (baselineY - BOX.paddingTop);
          return (
            <line
              key={tick}
              x1={0}
              x2={BOX.width}
              y1={y}
              y2={y}
              stroke="#E2E8F0"
              strokeWidth="1"
              strokeDasharray={tick === 0.5 ? "0" : "2 4"}
            />
          );
        })}

        {bars.map((height, index) => {
          if (height <= 0) return null;
          const x = projected[index]?.x ?? 0;
          return (
            <rect
              key={series[index].key}
              x={x - 3}
              y={baselineY - height}
              width={6}
              height={height}
              rx={2}
              fill="#38BDF8"
              opacity={hover === index ? 0.85 : 0.5}
            />
          );
        })}

        {observedRuns.map((run, index) =>
          run.length > 1 ? (
            <path
              key={`obs-${index}`}
              d={areaPath(run, baselineY)}
              fill="url(#ripple-area)"
            />
          ) : null,
        )}

        {observedRuns.map((run, index) =>
          run.length > 1 ? (
            <path
              key={`obspath-${index}`}
              d={polylinePath(run)}
              fill="none"
              stroke="#0F172A"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ) : null,
        )}

        {hasForecast && (
          <path
            d={polylinePath(forecastRun)}
            fill="none"
            stroke="#EA580C"
            strokeWidth="2"
            strokeDasharray="5 4"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}

        {lastObserved && hasForecast && (
          <line
            x1={lastObserved.x}
            x2={lastObserved.x}
            y1={BOX.paddingTop - 6}
            y2={baselineY}
            stroke="#94A3B8"
            strokeWidth="1"
            strokeDasharray="3 3"
          />
        )}

        {projected.map((point, index) => {
          const point_ = series[index];
          if (!point_ || point_.risk == null) return null;
          const isForecast = point_.kind === "forecast";
          const isNow = index === nowIndex;
          return (
            <circle
              key={`dot-${point_.key}`}
              cx={point.x}
              cy={point.y}
              r={isNow ? 4 : isForecast ? 2.4 : 2.8}
              fill={isNow ? accent : isForecast ? "#EA580C" : "#FFFFFF"}
              stroke={isForecast ? "#EA580C" : "#0F172A"}
              strokeWidth={isNow ? 2 : 1.4}
            />
          );
        })}

        {hover != null && activePoint && (
          <line
            x1={activePoint.x}
            x2={activePoint.x}
            y1={BOX.paddingTop - 6}
            y2={baselineY}
            stroke="#0F172A"
            strokeWidth="1"
            opacity="0.45"
          />
        )}

        <text x={2} y={BOX.paddingTop - 4} fontSize="8" fill="#94A3B8">
          higher
        </text>
        <text x={2} y={baselineY + 9} fontSize="8" fill="#94A3B8">
          cleaner
        </text>
      </svg>

      {active && activePoint && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-md bg-slate-900/92 px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap text-white shadow"
          style={{
            left: `${(activePoint.x / BOX.width) * 100}%`,
            top: `${(Math.max(0, activePoint.y - 26) / BOX.height) * 100}%`,
          }}
        >
          {active.label} · {active.risk == null ? "—" : active.risk.toFixed(2)}
          {active.rain ? ` · ${active.rain.toFixed(1)} mm` : ""}
        </div>
      )}

      <div className="mt-0.5 flex justify-between text-[10px] text-slate-500">
        <span>{formatDate(series[0]?.date)}</span>
        {nowDate && <span className="font-medium text-slate-600">{formatDate(nowDate)}</span>}
        <span>{formatDate(series[series.length - 1]?.date)}</span>
      </div>

      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] leading-snug text-slate-500">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-0.5 w-4 rounded bg-slate-900" /> observed
        </span>
        {hasForecast && (
          <span className="inline-flex items-center gap-1">
            <span
              className="inline-block h-0.5 w-4 rounded"
              style={{
                backgroundImage:
                  "repeating-linear-gradient(90deg,#EA580C 0 3px,transparent 3px 6px)",
              }}
            />
            runoff forecast
          </span>
        )}
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-2 w-1.5 rounded-sm bg-sky-400/70" /> rain
        </span>
        {band && (
          <span className="ml-auto inline-flex items-center gap-1 font-medium text-slate-600">
            <span className="h-2 w-2 rounded-full" style={{ background: accent }} />
            {band.label} now
          </span>
        )}
      </p>

      {forecastValues.length > 0 && (
        <p className="mt-1 text-[10px] leading-snug text-slate-500">
          Forecast peaks at <span className="font-semibold">{peak.toFixed(2)}</span> over the
          next {forecastValues.length} day{forecastValues.length === 1 ? "" : "s"}. Rain
          drives sediment and nutrient loading with a 1–3 day lag.
        </p>
      )}

      {/* Colour key: lets a value on the line be read against the map ramp in place. */}
      <div className="mt-1.5 flex gap-0.5">
        {Array.from({ length: 24 }, (_, index) => (
          <span
            key={index}
            className="h-1 flex-1 rounded-full"
            style={{ background: riskColor(index / 23) }}
          />
        ))}
      </div>
    </div>
  );
}
