import type { Confidence } from "./chartGeometry";

const STYLES: Record<Confidence, { label: string; className: string }> = {
  high: { label: "High confidence", className: "bg-emerald-100 text-emerald-800" },
  medium: { label: "Medium confidence", className: "bg-amber-100 text-amber-800" },
  low: { label: "Low confidence", className: "bg-rose-100 text-rose-800" },
  none: { label: "No recent data", className: "bg-slate-200 text-slate-700" },
};

export function ConfidenceBadge({ level }: { level: Confidence }) {
  const style = STYLES[level];
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${style.className}`}
    >
      {style.label}
    </span>
  );
}
