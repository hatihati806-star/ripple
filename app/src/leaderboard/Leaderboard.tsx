import { useEffect, useMemo, useState } from "react";
import type { Manifest, WaterBody } from "../domain/types";
import { countDemoted, formatRisk, rankBodies } from "../water/ranking";

interface LeaderboardProps {
  bodies: WaterBody[];
  selectedId: string | null;
  discovery?: Manifest["discovery"];
  onSelect: (id: string) => void;
  onClose: () => void;
}

type Order = "dirtiest" | "cleanest";

/**
 * Ranked readings across the catalogued water bodies.
 *
 * This exists because the map answers "where is colour" but not "which lake is worst".
 * The ranked number is each body's **areal mean** over its own footprint, the same
 * quantity the detail panel reports, not the worst single cell in it. Bodies whose
 * reading is flagged as unrepresentative -- peak-driven, thinly sampled, or carrying an
 * index caution such as Great Salt Lake's hypersalinity -- are listed after the ranked
 * ones rather than silently mixed in.
 */
export function Leaderboard({
  bodies,
  selectedId,
  discovery,
  onSelect,
  onClose,
}: LeaderboardProps) {
  const [order, setOrder] = useState<Order>("dirtiest");
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setMounted(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const ranked = useMemo(() => rankBodies(bodies, order), [bodies, order]);
  const sampled = ranked.length;
  const total = bodies.length;
  const demoted = useMemo(() => countDemoted(bodies), [bodies]);
  const minAreaKm2 = discovery ? Math.round(discovery.min_body_km2) : null;
  const rows = ranked.slice(0, 12);

  return (
    <section
      className="pointer-events-auto flex max-h-[42vh] w-full animate-panel-in flex-col rounded-xl bg-white/92 shadow-lg backdrop-blur"
      aria-label="Water body ranking"
    >
      <header className="border-b border-slate-200/70 px-3 py-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">
              Water bodies now
            </h2>
            <p className="text-[10px] text-slate-500">
              {sampled} of {total} bodies have a usable reading, ranked by each body&apos;s
              mean over its own water
              {minAreaKm2 ? ` · detected from the mask at ≥${minAreaKm2} km²` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Hide ranking"
            className="-mr-1 -mt-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
        <div
          className="mt-0.5 flex rounded-lg bg-slate-100 p-0.5"
          role="group"
          aria-label="Ranking order"
        >
          {(["dirtiest", "cleanest"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setOrder(option)}
              aria-pressed={order === option}
              className={`h-11 flex-1 rounded-md text-[11px] font-semibold capitalize transition ${
                order === option
                  ? "bg-white text-slate-800 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {option}
            </button>
          ))}
        </div>
      </header>

      <ol className="panel-scroll min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
        {rows.map((entry, index) => {
          const selected = entry.body.id === selectedId;
          const flag = entry.flags[0];
          const listed = index + 1;
          return (
            <li key={entry.body.id}>
              <button
                type="button"
                onClick={() => onSelect(entry.body.id)}
                aria-current={selected}
                className={`group flex min-h-11 w-full items-start gap-2 rounded-lg px-1.5 py-2.5 text-left transition ${
                  selected ? "bg-slate-900 text-white" : "hover:bg-slate-100/80"
                }`}
              >
                <span
                  className={`w-4 shrink-0 pt-0.5 text-right text-[10px] font-semibold tabular-nums ${
                    selected ? "text-white/70" : "text-slate-400"
                  }`}
                >
                  {listed}
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className={`flex items-baseline gap-1.5 text-[11.5px] font-medium ${
                      selected ? "text-white" : "text-slate-700"
                    }`}
                  >
                    <span className="truncate">{entry.body.name}</span>
                    {entry.body.area_km2 != null && (
                      <span
                        className={`shrink-0 text-[9.5px] tabular-nums ${
                          selected ? "text-white/60" : "text-slate-400"
                        }`}
                      >
                        {entry.body.area_km2 >= 1000
                          ? `${(entry.body.area_km2 / 1000).toFixed(1)}k km²`
                          : `${Math.round(entry.body.area_km2)} km²`}
                      </span>
                    )}
                  </span>
                  <span
                    className="mt-1 block h-1.5 overflow-hidden rounded-full"
                    style={{ background: selected ? "rgba(255,255,255,0.18)" : "#F1F5F9" }}
                  >
                    <span
                      className="block h-full rounded-full transition-[width] duration-700 ease-out"
                      style={{
                        width: mounted ? `${Math.max(4, entry.risk * 100)}%` : "0%",
                        background: entry.band.color,
                        transitionDelay: `${index * 28}ms`,
                      }}
                    />
                  </span>
                  {flag && (
                    <span
                      className={`mt-1 block text-[9.5px] leading-snug ${
                        selected
                          ? "text-amber-200"
                          : flag.demotes
                            ? "text-amber-700"
                            : "text-slate-500"
                      }`}
                      title={flag.hint}
                    >
                      {flag.label}
                      {flag.demotes ? " · listed after the ranked readings" : ""}
                      {" — "}
                      {flag.hint}
                    </span>
                  )}
                </span>
                <span className="shrink-0 pt-0.5 text-right">
                  <span
                    className={`block text-[11px] font-semibold tabular-nums ${
                      selected ? "text-white" : "text-slate-600"
                    }`}
                  >
                    {formatRisk(entry.risk)}
                  </span>
                  {entry.peak != null && entry.peak - entry.risk >= 0.05 && (
                    <span
                      className={`block text-[9px] tabular-nums ${
                        selected ? "text-white/60" : "text-slate-400"
                      }`}
                      title="worst single cell in this body"
                    >
                      peak {formatRisk(entry.peak)}
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
        {rows.length === 0 && (
          <li className="px-2 py-3 text-[11px] text-slate-500">
            No catalogued body has a valid reading in the latest composite.
          </li>
        )}
      </ol>

      <footer className="border-t border-slate-200/70 px-3 py-1.5 text-[10px] leading-snug text-slate-400">
        {demoted > 0
          ? `${demoted} of ${sampled} readings are flagged and listed after the ranked ones · `
          : ""}
        Bodies are found by sieving the composited water mask, not from a hand-written list
        · click one to fly to it
      </footer>
    </section>
  );
}
