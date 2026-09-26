/**
 * Retrieval over the shipped fact base.
 *
 * This is the assistant's entire knowledge. It is deterministic: the same question always
 * produces the same records, so an answer can be reproduced and audited, and so a test can
 * assert exactly which records a question pulls in. Nothing here composes new facts — it
 * formats records that the pipeline wrote.
 */
import { BODY_ALIASES } from "./guard.js";
import type { FactBase, FactBody, Retrieved, Source } from "./types";

/** Words that appear in body names but do not identify one. */
const GENERIC_NAME_TOKENS = new Set([
  "lake", "lakes", "reservoir", "bay", "water", "body", "river", "pond", "sea", "great",
  "north", "south", "east", "west", "upper", "lower", "big", "little", "of", "the", "and",
]);

export const MAX_BODIES = 4;
export const MAX_CONTEXT_CHARS = 24_000;
/** How many entries of the ranked list to include for "worst"/"cleanest" questions. */
export const RANK_LIST = 8;

const RANK_WORDS = ["worst", "dirtiest", "cleanest", "best", "rank", "ranking", "top",
  "leaderboard", "most", "least", "polluted"];
const FORECAST_WORDS = ["forecast", "outlook", "rain", "rainfall", "runoff", "storm",
  "wet", "dry", "next week", "7-day", "seven", "tomorrow", "week"];
const TURBIDITY_WORDS = ["turbidity", "turbid", "sediment", "suspended", "ndti", "silt",
  "brown", "muddy"];
const BLOOM_WORDS = ["bloom", "algae", "algal", "chlorophyll", "ndci", "eutrophic",
  "cyanobacteria"];
const VALIDATION_WORDS = ["valid", "validation", "validated", "accuracy", "accurate",
  "gauge", "gauges", "usgs", "ground truth", "in-situ", "in situ", "error", "correlat"];
const METHOD_WORDS = ["how", "method", "methodology", "mean", "average", "index", "ndci",
  "ndti", "resolution", "pixel", "cell", "measure", "measured", "why", "what does",
  "explain", "work", "works", "source", "sources", "data come"];

function tokens(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/[\s-]+/)
    .filter(Boolean);
}

/** How well a body's name matches the question. 0 means no match. */
export function scoreBody(body: FactBody, text: string, asked: string[]): number {
  const name = body.name.toLowerCase();
  if (text.includes(name)) return 100;
  const askedSet = new Set(asked);
  const distinctive = tokens(body.name).filter(
    (token) => token.length >= 4 && !GENERIC_NAME_TOKENS.has(token),
  );
  let score = 0;
  for (const token of distinctive) {
    if (askedSet.has(token)) score += 10;
    else if (text.includes(token)) score += 6;
  }
  if (askedSet.has(body.id.toLowerCase())) score += 20;
  return score;
}

function aliasIds(text: string): string[] {
  return Object.entries(BODY_ALIASES)
    .filter(([alias]) => text.includes(alias))
    .map(([, id]) => id);
}

function hasAny(text: string, words: string[]): boolean {
  return words.some((word) => text.includes(word));
}

function bodyBlock(body: FactBody): string {
  const lines = [
    `BODY ${body.name} (id ${body.id}, ${body.kind}, ${body.area_km2 ?? "?"} km2, ` +
      `${body.curated ? "named from the catalog" : "named by coordinates"}` +
      `${body.caution ? `, CAUTION: ${body.caution}` : ""})`,
  ];
  for (const sample of body.observed) {
    lines.push(
      `  observed ${sample.frame} ${sample.date ?? ""}: body mean risk ${sample.risk}, ` +
        `worst cell ${sample.risk_peak}, sampled cells ${sample.cells}, ` +
        `valid ${sample.valid}`,
    );
  }
  if (body.latest) {
    lines.push(
      `  latest: ${body.latest.frame} (${body.latest.date}) risk ${body.latest.risk}, ` +
        `peak ${body.latest.risk_peak}, cells ${body.latest.cells}`,
    );
  }
  if (body.forecast) {
    const f = body.forecast;
    lines.push(
      `  forecast: baseline risk ${f.baseline_risk}, antecedent rain ` +
        `${f.antecedent_rain_mm} mm, curve number ${f.curve_number}, ` +
        `land cover ${f.land_cover}, soil group ${f.soil_group}`,
    );
    lines.push(`  forecast daily rain mm: ${(f.rain_mm ?? []).join(", ")}`);
    lines.push(`  forecast daily runoff mm: ${(f.runoff_mm ?? []).join(", ")}`);
    lines.push(`  forecast daily risk: ${(f.risk ?? []).join(", ")}`);
  } else {
    lines.push("  forecast: none in the shipped data for this body");
  }
  return lines.join("\n");
}

function datasetBlock(facts: FactBase): string {
  const d = facts.dataset;
  const observed = d.frames.filter((frame) => frame.kind === "observed");
  const forecast = d.frames.filter((frame) => frame.kind === "forecast");
  return [
    "DATASET",
    `  ${d.name}: ${d.one_liner}`,
    `  region ${d.region_name} (bbox ${(d.region_bbox ?? []).join(", ")}), ` +
      `cell ${d.resolution_m?.toFixed(0)} m`,
    `  generated_utc ${facts.generated_utc}`,
    `  ${d.body_count} bodies catalogued, ${d.bodies_with_a_reading} with a reading, ` +
      `${d.named_from_catalog} named from the catalog`,
    `  observed frames: ${observed
      .map((frame) => `${frame.id} ${frame.date} (${frame.window_label})`)
      .join("; ")}`,
    `  forecast frames: ${forecast
      .map((frame) => `${frame.id} ${frame.label} ${frame.date}`)
      .join("; ")}`,
    `  ranked value: ${d.body_statistic}`,
    `  colour: ${d.ramp_semantics}`,
    `  data sources: ${d.data_sources.join("; ")}`,
    `  body statistics note: ${d.body_stats_method}`,
  ].join("\n");
}

function methodBlock(facts: FactBase): string {
  const m = facts.method;
  return [
    "METHOD AND LIMITS",
    ...Object.entries(m.indices).map(([key, value]) => `  ${key}: ${value}`),
    `  bloom threshold NDCI: ${facts.dataset.bloom_threshold_ndci}`,
    `  forecast basis: ${m.forecast_basis}`,
    `  forecast dates: ${(m.forecast_dates ?? []).join(", ")}`,
    `  forecast regional rain mm/day: ${(m.forecast_region_rain_mm ?? []).join(", ")}`,
    `  forecast assumptions: ${JSON.stringify(m.forecast_assumptions ?? {})}`,
    "  limits the dataset itself states:",
    ...m.limits.map((limit) => `    - ${limit}`),
    `  disclaimer: ${m.disclaimer}`,
  ].join("\n");
}

function validationBlock(facts: FactBase): string {
  const v = facts.validation;
  if (!v) return "VALIDATION\n  none shipped";
  return [
    "VALIDATION",
    `  ${v.what}`,
    `  window ${v.window?.join(" to ")}, ${v.pairs} matched pairs at ${v.gauges} gauges, ` +
      `in-situ turbidity ${v.turbidity_range_fnu?.join(" to ")} FNU`,
    `  published cell value: Spearman rho ${v.published_cell_value.spearman_rho} ` +
      `(p ${v.published_cell_value.spearman_p}), leave-one-out factor ` +
      `${v.published_cell_value.leave_one_out_factor}`,
    `  water-only sub-pixels: Spearman rho ${v.water_only.spearman_rho} ` +
      `(p ${v.water_only.spearman_p}), leave-one-out factor ` +
      `${v.water_only.leave_one_out_factor}`,
    ...Object.entries(v.strata).map(
      ([label, block]) =>
        `  cells ${label} water: ${block.pairs} pairs at ${block.sites} gauges, ` +
        `rho ${block.spearman_rho}, leave-one-out factor ${block.leave_one_out_factor}`,
    ),
    ...v.honest_limits.map((limit) => `  limit: ${limit}`),
    `  full table: ${v.source}`,
  ].join("\n");
}

function rankBlock(facts: FactBase): string {
  const measured = facts.bodies
    .filter((body) => body.latest && body.latest.risk != null)
    .sort((a, b) => (b.latest!.risk ?? 0) - (a.latest!.risk ?? 0));
  const line = (body: FactBody, rank: number) =>
    `  ${rank}. ${body.name} = ${body.latest!.risk}` +
    `${body.curated ? "" : " (named by coordinates, not from the catalog)"}`;
  // Explicit ranks and an explicit named/unnamed marker: an earlier version printed a bare
  // "name value; name value" list and the model read the order wrong, then contradicted
  // itself about which body was highest.
  const top = measured.slice(0, RANK_LIST).map((body, index) => line(body, index + 1));
  const bottom = measured
    .slice(-RANK_LIST)
    .map((body, index) => line(body, measured.length - RANK_LIST + index + 1));
  return [
    `RANKING: areal mean risk over each body's own footprint, highest first, of ` +
      `${measured.length} bodies with a reading. This list is already in rank order.`,
    "  highest:",
    ...top,
    "  lowest:",
    ...bottom,
    "  The catalogue names 49 bodies; the rest are named by their coordinates, which is " +
      "why some entries read \"Water body · 41.7°N 120.1°W\".",
  ].join("\n");
}

/**
 * Pick the records that answer this question.
 *
 * Body records come first and are capped; the dataset and method blocks are always present
 * because they carry the limits the answer must respect. Validation and ranking are added
 * when the question asks for them, so a "which lake is worst" question is not answered from
 * the model's impression of the leaderboard.
 */
export function retrieve(facts: FactBase, question: string): Retrieved {
  const text = question.toLowerCase();
  const asked = tokens(question);
  const wanted = new Set(aliasIds(text));

  const scored = facts.bodies
    .map((body) => ({ body, score: scoreBody(body, text, asked) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.body.name.localeCompare(b.body.name));
  for (const entry of scored.slice(0, MAX_BODIES)) wanted.add(entry.body.id);

  const matched = facts.bodies.filter((body) => wanted.has(body.id)).slice(0, MAX_BODIES);

  const blocks: string[] = [datasetBlock(facts)];
  const sources: Source[] = [
    { label: `${facts.dataset.name} dataset record`, kind: "dataset" },
  ];

  for (const frame of facts.dataset.frames) {
    sources.push({ label: `${frame.id} · ${frame.date}`, kind: "frame" });
  }

  for (const body of matched) {
    blocks.push(bodyBlock(body));
    sources.push({
      label: `${body.name} · ${body.latest?.frame ?? "no reading"}`,
      kind: "body",
    });
  }

  const includeMethod = hasAny(text, METHOD_WORDS) || matched.length === 0;
  if (includeMethod) {
    blocks.push(methodBlock(facts));
    sources.push({ label: "method and limits", kind: "method" });
  }
  if (hasAny(text, VALIDATION_WORDS)) {
    blocks.push(validationBlock(facts));
    sources.push({ label: "USGS validation", kind: "validation" });
  }
  if (hasAny(text, RANK_WORDS)) {
    blocks.push(rankBlock(facts));
    sources.push({ label: "ranked bodies", kind: "dataset" });
  }
  if (!includeMethod
      && (hasAny(text, FORECAST_WORDS) || hasAny(text, TURBIDITY_WORDS)
          || hasAny(text, BLOOM_WORDS))) {
    // The index definitions and the forecast basis are what make those columns readable,
    // and they carry the limits that must be repeated with the answer.
    blocks.push(methodBlock(facts));
    sources.push({ label: "method and limits", kind: "method" });
  }

  let context = blocks.join("\n\n");
  if (context.length > MAX_CONTEXT_CHARS) {
    context = `${context.slice(0, MAX_CONTEXT_CHARS)}\n[records truncated]`;
  }

  return {
    context,
    sources,
    matchedBodies: matched.map((body) => body.name),
    blocks,
  };
}
