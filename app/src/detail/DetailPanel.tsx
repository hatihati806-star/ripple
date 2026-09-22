import { useMemo, useState, type ReactNode } from "react";
import type { Frame, Manifest, Selection, WaterBody } from "../domain/types";
import { riskBand } from "../domain/palette";
import { confidenceFromAge } from "./chartGeometry";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { ForecastChart, type ChartPointInput } from "./ForecastChart";
import {
  assessBody,
  bloomFlag,
  bodyForecast,
  bodyHistory,
  formatAge,
  formatDate,
  formatPercent,
  formatRisk,
  latestAvailable,
} from "../water/ranking";
import type { ProbeResult } from "../inspect/probe";

interface DetailPanelProps {
  manifest: Manifest;
  frames: Frame[];
  frameIndex: number;
  selection: Selection;
  body: WaterBody | null;
  probe: ProbeResult | null;
  probeStatus: "idle" | "loading" | "ready" | "error";
  probeSampleM: number | null;
  onClose: () => void;
  /** Opens the full-screen 3D analysis; only meaningful for a catalogued body. */
  onOpen3D?: () => void;
}

interface StatProps {
  label: string;
  value: string;
  hint?: string;
}

function Stat({ label, value, hint }: StatProps) {
  return (
    <div className="rounded-lg bg-slate-50 px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-[12px] font-semibold text-slate-800 tabular-nums">{value}</div>
      {hint && <div className="text-[9px] leading-tight text-slate-400">{hint}</div>}
    </div>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
      {children}
    </h3>
  );
}

/**
 * Detail panel for the selected water body or inspected point.
 *
 * The two selection kinds deliberately read differently: a catalogued body has exact
 * per-frame NDCI/NDTI and a forecast; an arbitrary point is sampled from the probe rasters
 * and is reported as a band with its footprint stated, because a single probe pixel is not
 * a measurement.
 */
export function DetailPanel({
  manifest,
  frames,
  frameIndex,
  selection,
  body,
  probe,
  probeStatus,
  probeSampleM,
  onClose,
  onOpen3D,
}: DetailPanelProps) {
  const [notesOpen, setNotesOpen] = useState(false);
  const frame = frames[frameIndex] ?? null;
  const observed = useMemo(
    () => frames.filter((entry) => entry.kind === "observed"),
    [frames],
  );

  const chartPoints = useMemo<ChartPointInput[]>(() => {
    if (body) {
      const history = bodyHistory(body, frames);
      const forecast = bodyForecast(body, frames);
      return [
        ...history.map(({ frame: historyFrame, risk }) => ({
          key: historyFrame.id,
          label: formatDate(historyFrame.date),
          date: historyFrame.date,
          risk,
          kind: "observed" as const,
        })),
        ...forecast.map(({ frame: forecastFrame, risk }) => ({
          key: forecastFrame.id,
          label: forecastFrame.label,
          date: forecastFrame.date,
          risk,
          kind: "forecast" as const,
          rain: body.rain_mm[(forecastFrame.forecast_day ?? 1) - 1] ?? null,
        })),
      ];
    }
    if (probe) {
      return frames.map((entry, index) => ({
        key: entry.id,
        label: entry.label,
        date: entry.date,
        risk: probe.risk[index] ?? null,
        kind: entry.kind,
        rain:
          entry.kind === "forecast"
            ? (manifest.forecast?.region_rain_mm[(entry.forecast_day ?? 1) - 1] ?? null)
            : null,
      }));
    }
    return [];
  }, [body, probe, frames, manifest.forecast]);

  // Every chart here ends its observed segment at the latest composite, so "now" is the
  // last observed frame regardless of which frame the map is currently showing.
  const boundaryIndex = Math.max(0, observed.length - 1);

  /** Most recent real observation, even when the displayed frame has no valid water. */
  const lastReading = useMemo(
    () => (body ? latestAvailable(body, frames) : null),
    [body, frames],
  );

  const currentRisk = useMemo(() => {
    if (body) return body.latest_risk;
    if (probe) return probe.risk[frameIndex] ?? null;
    return null;
  }, [body, probe, frameIndex]);

  const showingReading =
    body && lastReading && (currentRisk == null || currentRisk !== lastReading.risk)
      ? lastReading
      : null;
  const shownRisk = currentRisk ?? showingReading?.risk ?? null;
  const bodyFlags = useMemo(() => (body ? assessBody(body) : []), [body]);
  const demotingFlags = bodyFlags.filter((flag) => flag.demotes);

  const band = riskBand(shownRisk);
  const bloom = bloomFlag(body?.latest_ndci, manifest.bloom_threshold_ndci);
  const history = body ? bodyHistory(body, frames) : [];
  const spread = useMemo(() => {
    const values = history
      .map((entry) => entry.risk)
      .filter((value): value is number => value != null);
    if (values.length < 2) return null;
    return { min: Math.min(...values), max: Math.max(...values), count: values.length };
  }, [history]);

  const confidence = confidenceFromAge(frame?.age_days);
  const title = body
    ? body.name
    : selection?.kind === "point"
      ? "Inspected point"
      : "Details";

  return (
    <aside
      className="pointer-events-auto w-full animate-panel-in rounded-xl bg-white/95 p-3 shadow-lg backdrop-blur"
      aria-label="Water body details"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-slate-800">{title}</h2>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
            {band ? (
              <span
                className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                style={{ background: `${band.color}1a`, color: band.color }}
              >
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: band.color }} />
                {band.label} · {formatRisk(shownRisk)}
              </span>
            ) : (
              <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">
                No reading on this frame
              </span>
            )}
            {bloom && (
              <span
                className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                  bloom.label === "Probable bloom"
                    ? "bg-rose-50 text-rose-700"
                    : "bg-slate-100 text-slate-500"
                }`}
                title={bloom.hint}
              >
                {bloom.label}
              </span>
            )}
            <ConfidenceBadge level={confidence} />
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          <span aria-hidden="true">✕</span>
        </button>
      </div>

      {band && <p className="mt-1.5 text-[10px] leading-snug text-slate-500">{band.hint}.</p>}

      {body && onOpen3D && (
        <button
          type="button"
          onClick={onOpen3D}
          className="mt-2 flex min-h-11 w-full items-center justify-center rounded-lg bg-slate-900 px-3 text-[11px] font-semibold text-white transition hover:bg-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          View this lake in 3D
        </button>
      )}
      {showingReading && (
        <p className="mt-1 text-[10px] leading-snug text-amber-700">
          No valid water in the frame on screen — showing the last real observation,{" "}
          {formatDate(showingReading.frame.date)}.
        </p>
      )}

      <div className="mt-2.5 grid grid-cols-2 gap-2">
        {body ? (
          <>
            <Stat
              label="Body mean · ranked"
              value={formatRisk(body.latest_risk)}
              hint="mean over this body's own water"
            />
            <Stat
              label="Peak cell"
              value={formatRisk(body.latest_peak ?? undefined)}
              hint="worst single cell, not the ranked value"
            />
            <Stat
              label="NDCI at sample pixel"
              value={formatRisk(body.latest_ndci)}
              hint={bloom?.hint ?? "chlorophyll-a proxy, one cell"}
            />
            <Stat
              label="NDTI at sample pixel"
              value={formatRisk(body.latest_ndti)}
              hint="suspended sediment proxy, one cell"
            />
            <Stat
              label="Sampled cells"
              value={
                body.latest_cells != null
                  ? `${body.latest_cells}${
                      body.latest_cell_fraction != null
                        ? ` · ${formatPercent(body.latest_cell_fraction)}`
                        : ""
                    }`
                  : "—"
              }
              hint="cells of this body with a reading"
            />
            <Stat
              label="Observed history"
              value={spread ? `${formatRisk(spread.min)} → ${formatRisk(spread.max)}` : "—"}
              hint={spread ? `${spread.count} composites` : "no frames"}
            />
          </>
        ) : (
          <>
            <Stat
              label="Sample footprint"
              value={probeSampleM ? `~${(probeSampleM / 1000).toFixed(1)} km` : "—"}
              hint="neighbourhood averaged"
            />
            <Stat
              label="Water here"
              value={probe?.valid ? "Yes" : "Not detected"}
              hint="on the composited mask"
            />
            <Stat
              label="Index scale"
              value="relative 0–1"
              hint="vs region p05–p95"
            />
            <Stat label="Chart series" value={`${observed.length} frames`} hint="observed composites" />
          </>
        )}
      </div>

      {body && demotingFlags.length > 0 && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50/70 px-2 py-1.5">
          <h4 className="text-[10px] font-semibold uppercase tracking-wide text-amber-800">
            Reading flagged
          </h4>
          <ul className="mt-0.5 space-y-0.5">
            {demotingFlags.map((flag) => (
              <li key={flag.kind} className="text-[10px] leading-snug text-amber-800">
                <span className="font-semibold">{flag.label}:</span> {flag.hint}.
              </li>
            ))}
          </ul>
          <p className="mt-0.5 text-[9.5px] leading-snug text-amber-700">
            Flagged bodies are listed after the ranked ones in the leaderboard, in both
            directions, so an unrepresentative reading cannot be presented as the dirtiest
            or the cleanest.
          </p>
        </div>
      )}

      <div className="mt-3">
        <SectionTitle>
          {body ? "History & 7-day outlook" : "Observed history"}
        </SectionTitle>
        {probeStatus === "loading" && !body ? (
          <div className="flex h-24 items-center justify-center rounded-lg bg-slate-50 text-[11px] text-slate-500">
            <span className="mr-2 h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
            Sampling all frames…
          </div>
        ) : (
          <ForecastChart
            points={chartPoints}
            boundary={boundaryIndex}
            emptyHint={
              probeStatus === "error"
                ? "Could not load the probe tiles for this point."
                : "This point reads as land or cloud on every composited frame, so there is no series to show."
            }
          />
        )}
      </div>

      {body && (
        <p className="mt-2 text-[10px] leading-snug text-slate-500">
          {body.kind === "reservoir" ? "Reservoir" : body.kind === "bay" ? "Bay" : "Lake"} ·{" "}
          {body.area_km2 != null ? `${Math.round(body.area_km2).toLocaleString("en-US")} km² · ` : ""}
          {body.lat.toFixed(2)}°, {body.lon.toFixed(2)}°
          {frame?.scene_count ? ` · ${frame.scene_count} scenes in this composite` : ""}
          {frame?.age_days != null ? ` · ${formatAge(frame.age_days)}` : ""}
        </p>
      )}

      {body && !body.curated && (
        <p className="mt-1 text-[10px] leading-snug text-slate-400">
          Found by the mask discovery pass, not a named catalog entry — named by its
          coordinates.
        </p>
      )}

      {body?.forecast && (
        <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50/70 p-2">
          <div className="flex items-center justify-between">
            <h4 className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              Forecast inputs
            </h4>
            <span className="text-[9px] text-slate-400">Open-Meteo at this location</span>
          </div>
          <dl className="mt-1 grid grid-cols-3 gap-1.5 text-[10px]">
            <div>
              <dt className="text-slate-500">Rain, 7 d</dt>
              <dd className="font-semibold tabular-nums text-slate-700">
                {body.forecast.rain_mm.reduce((sum, mm) => sum + mm, 0).toFixed(1)} mm
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Antecedent</dt>
              <dd className="font-semibold tabular-nums text-slate-700">
                {body.forecast.antecedent_rain_mm.toFixed(1)} mm
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Curve no.</dt>
              <dd className="font-semibold tabular-nums text-slate-700">
                {body.forecast.curve_number.toFixed(0)}
              </dd>
            </div>
          </dl>
          <p className="mt-1 text-[9.5px] leading-snug text-slate-500">
            Runoff {body.forecast.runoff_mm.map((mm) => mm.toFixed(1)).join("/")} mm over 7
            days, from SCS curve number on this body’s own rainfall. Zero runoff is a real
            result: rain below the initial abstraction does not mobilise sediment.
          </p>
        </div>
      )}

      {!body && selection?.kind === "point" && (
        <p className="mt-2 text-[10px] leading-snug text-slate-500">
          {selection.lat.toFixed(3)}°, {selection.lon.toFixed(3)}° · sampled from the decimated
          probe rasters, not the display tiles.
        </p>
      )}

      {frame && (
        <p className="mt-1 text-[10px] leading-snug text-slate-500">
          {frame.kind === "observed"
            ? `Composite window ${frame.window_label} · ${formatPercent(frame.coverage?.satellite_valid_fraction)} of the region had usable imagery`
            : `Forecast day ${frame.forecast_day} · ${manifest.forecast?.region_rain_mm[(frame.forecast_day ?? 1) - 1]?.toFixed(1) ?? "0"} mm regional rain`}
        </p>
      )}

      <button
        type="button"
        onClick={() => setNotesOpen((open) => !open)}
        aria-expanded={notesOpen}
        className="mt-2 inline-flex min-h-11 items-center rounded text-[10px] font-semibold text-slate-500 underline decoration-dotted underline-offset-2 hover:text-slate-700"
      >
        {notesOpen ? "Hide method & limits" : "Method & limits"}
      </button>

      {notesOpen && (
        <div className="mt-1.5 space-y-1 border-t border-slate-200 pt-2 text-[10px] leading-snug text-slate-500">
          {manifest.forecast && (
            <p>
              <span className="font-semibold text-slate-600">Forecast:</span>{" "}
              {manifest.forecast.basis}
            </p>
          )}
          <p>
            <span className="font-semibold text-slate-600">Colour:</span>{" "}
            {manifest.ramp_semantics ?? "relative to the region's observed range"}. Both
            NDCI and NDTI are normalized the same way, so the ramp means one thing
            everywhere in a frame.
          </p>
          <p>
            <span className="font-semibold text-slate-600">Body readings:</span>{" "}
            {manifest.body_statistic ??
              "the ranked value is the areal mean over the body's own footprint"}{" "}
            The NDCI and NDTI shown above are the sampled pixel, not the body mean, so
            they can be compared with a single in-situ gauge.
          </p>
          <p>
            <span className="font-semibold text-slate-600">Not covered:</span> toxins,
            bacteria, metals, cyanobacteria. Optical proxies cannot see them. Rivers under
            ~50 m are 1–3 pixels and are excluded by the water mask.
          </p>
          <p>{manifest.disclaimer}</p>
        </div>
      )}

      <p className="mt-2 border-t border-slate-200 pt-2 text-[10px] leading-snug text-slate-400">
        {manifest.disclaimer}
      </p>
    </aside>
  );
}
