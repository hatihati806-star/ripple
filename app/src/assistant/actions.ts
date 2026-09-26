/**
 * Actions the assistant can take, and the rules that keep them honest.
 *
 * The assistant drives the map, so an action is a claim about the world: "Flew to Georgian
 * Bay" must actually move the map to Georgian Bay. Every name a model produces is therefore
 * resolved against the shipped fact base before it becomes an action -- an unresolvable name
 * is dropped and reported, never passed to the UI to fail or, worse, to half-succeed.
 */
import type { FactBase, FactBody } from "./types";

export type ActionKind = "rank" | "outlook" | "fly_to" | "set_frame" | "open_3d";

export interface AssistantAction {
  kind: ActionKind;
  /** Chip text while the action is running ("Ranking 250 water bodies…"). */
  running: string;
  /** Chip text once it has completed ("Ranked 250 water bodies"). */
  done: string;
  /** Set for actions the *server* already performed; the client runs the rest. */
  payload?: unknown;
  /** Resolved body id, for the client to fly to or open in 3D. */
  bodyId?: string;
  /** Resolved frame id, for the client to step to. */
  frame?: string;
}

/** UI actions the browser performs; everything else is done before the answer is written. */
export const CLIENT_ACTIONS: ReadonlySet<ActionKind> = new Set<ActionKind>([
  "fly_to", "set_frame", "open_3d",
]);

const GENERIC = new Set([
  "lake", "lakes", "reservoir", "bay", "water", "body", "river", "pond", "sea", "great",
  "of", "the", "and", "north", "south", "east", "west", "upper", "lower",
]);

function tokens(text: string): string[] {
  return text.toLowerCase().replace(/[^a-z0-9\s-]/g, " ").split(/[\s-]+/).filter(Boolean);
}

/**
 * Find the body a model named, or nothing.
 *
 * Deliberately strict: an exact name, then an id, then a match on the body's *distinctive*
 * tokens (so "erie" resolves, "lake" does not). A loose match here would fly the map to the
 * wrong lake and then cite its numbers, which is worse than no action at all.
 */
export function resolveBody(facts: FactBase, name: string): FactBody | null {
  const wanted = name.trim().toLowerCase();
  if (!wanted) return null;
  const exact = facts.bodies.find((body) => body.name.toLowerCase() === wanted);
  if (exact) return exact;
  const byId = facts.bodies.find((body) => body.id.toLowerCase() === wanted);
  if (byId) return byId;

  const asked = tokens(wanted);
  const distinctive = asked.filter((token) => token.length >= 4 && !GENERIC.has(token));
  if (distinctive.length === 0) return null;
  const scored = facts.bodies
    .map((body) => {
      const nameTokens = new Set(tokens(body.name));
      const hits = distinctive.filter((token) => nameTokens.has(token)).length;
      return { body, hits };
    })
    .filter((entry) => entry.hits === distinctive.length)
    .sort((a, b) => b.hits - a.hits);
  return scored.length === 1 ? scored[0].body : null;
}

/**
 * Resolve a frame the model asked for: an id, "now", a relative day, or a date.
 *
 * The shipped forecast is labelled +1d..+7d but the *first* forecast frame carries the same
 * date as the latest observation, so "+7d" is `fc-07` and "now" is the latest observed frame.
 * Both are resolved here rather than left to the model's arithmetic.
 */
export function resolveFrame(facts: FactBase, spec: string): string | null {
  const wanted = spec.trim().toLowerCase();
  const frames = facts.dataset.frames;
  const observed = frames.filter((frame) => frame.kind === "observed");
  const forecast = frames.filter((frame) => frame.kind === "forecast");

  if (wanted === "now" || wanted === "latest" || wanted === "today") {
    return observed.at(-1)?.id ?? null;
  }
  const byId = frames.find((frame) => frame.id.toLowerCase() === wanted);
  if (byId) return byId.id;
  const byDate = frames.find((frame) => frame.date === spec.trim());
  if (byDate) return byDate.id;

  const day = /^\+?(\d{1,2})\s*d?$/.exec(wanted);
  if (day) {
    const index = Number(day[1]);
    if (index >= 1 && index <= forecast.length) return forecast[index - 1].id;
  }
  if (wanted === "end" || wanted === "last") return forecast.at(-1)?.id ?? null;
  return null;
}

/** Human label for a frame id, for the chip text. */
export function frameLabel(facts: FactBase, frameId: string): string {
  const frame = facts.dataset.frames.find((entry) => entry.id === frameId);
  if (!frame) return frameId;
  return frame.kind === "forecast" ? `${frame.label} · ${frame.date}` : `${frame.date}`;
}

/**
 * The ranked list, computed from the fact base rather than recalled by the model.
 * Returns the same numbers the leaderboard shows.
 */
export function rankBodies(facts: FactBase, order: "worst" | "cleanest", limit: number) {
  const measured = facts.bodies
    .filter((body) => body.latest && body.latest.risk != null)
    .sort((a, b) => (b.latest!.risk ?? 0) - (a.latest!.risk ?? 0));
  const list = order === "cleanest" ? [...measured].reverse() : measured;
  const capped = Math.min(Math.max(1, Math.round(limit || 8)), 12);
  return {
    total: measured.length,
    entries: list.slice(0, capped).map((body, index) => ({
      rank: order === "cleanest" ? measured.length - index : index + 1,
      name: body.name,
      risk: body.latest!.risk,
      frame: body.latest!.frame,
      date: body.latest!.date,
      named: body.curated,
      caution: body.caution ?? null,
    })),
  };
}

/** One body's observed series and forecast, as records rather than as prose. */
export function outlookFor(body: FactBody) {
  return {
    name: body.name,
    caution: body.caution ?? null,
    observed: body.observed.map((sample) => ({
      frame: sample.frame,
      date: sample.date,
      risk: sample.risk,
      peak: sample.risk_peak,
      cells: sample.cells,
    })),
    forecast: body.forecast
      ? {
          baseline_risk: body.forecast.baseline_risk,
          rain_mm: body.forecast.rain_mm,
          runoff_mm: body.forecast.runoff_mm,
          risk: body.forecast.risk,
        }
      : null,
  };
}
