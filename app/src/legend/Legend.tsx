import type { Manifest } from "../domain/types";
import { RISK_GRADIENT } from "../domain/palette";

interface LegendProps {
  manifest: Manifest | null;
  collapsed?: boolean;
  onToggle?: () => void;
}

/**
 * The legend always names the quantity and says which end is cleaner.
 *
 * Green here means "cleaner water", not "safe water" and not "light rain" -- the same
 * green-to-red ramp is used by weather radar for precipitation, and the reference image
 * that inspired this project reads green as light rain. Naming the quantity is the only
 * thing that prevents that misreading.
 */
export function Legend({ manifest, collapsed = false, onToggle }: LegendProps) {
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="pointer-events-auto flex min-h-11 items-center gap-2 rounded-xl bg-white/92 px-2.5 py-2 text-[11px] font-semibold text-slate-600 shadow-sm backdrop-blur transition hover:bg-white"
        aria-label="Show legend"
      >
        <span aria-hidden="true" className="h-2 w-8 rounded-full" style={{ background: RISK_GRADIENT }} />
        Legend
      </button>
    );
  }

  return (
    <div className="animate-panel-in rounded-xl bg-white/92 px-3 py-2.5 shadow-sm backdrop-blur">
      <div className="flex items-start justify-between gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">
          Water quality index
        </div>
        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            aria-label="Hide legend"
            className="-mr-1 -mt-1 flex h-11 w-11 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
          >
            <span aria-hidden="true">✕</span>
          </button>
        )}
      </div>

      <div className="relative mt-1.5">
        <div
          className="h-2.5 w-full min-w-44 rounded-full"
          style={{ background: RISK_GRADIENT }}
        />
        <div className="mt-1 flex w-full min-w-44 justify-between text-[10px] font-medium text-slate-500">
          <span>cleaner</span>
          <span>more polluted</span>
        </div>
      </div>

      <p className="mt-1 text-[10px] leading-snug text-slate-500">
        Greener is <span className="font-semibold text-slate-600">cleaner</span>, red is{" "}
        <span className="font-semibold text-slate-600">more polluted</span> — relative to this
        region’s own observed range
        {manifest?.resolution_m ? ` · ${Math.round(manifest.resolution_m)} m` : ""}
      </p>
      <p className="text-[10px] leading-snug text-slate-400">
        Optical proxy for algae &amp; sediment · not a health or drinking-water measurement
      </p>
    </div>
  );
}
