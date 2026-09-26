/**
 * The two deterministic guards that stand between the model and the user.
 *
 * A language model cannot be made incapable of inventing a number, so this module does not
 * try. Instead it makes invention *detectable*: every numeric token in an answer is checked
 * against the records the answer was supposed to come from, and anything that cannot be
 * traced back is reported rather than quietly rendered. The same applies to the topic: a
 * question outside the environment is refused before the model is even called, so the
 * refusal cannot drift with sampling.
 *
 * Both functions are pure and shared by the serverless function and its tests.
 */

/** Vocabulary that puts a question inside the assistant's remit. */
export const ENVIRONMENT_TERMS = [
  // water bodies and hydrology
  "lake", "lakes", "river", "rivers", "reservoir", "bay", "pond", "stream", "creek",
  "wetland", "watershed", "catchment", "basin", "delta", "estuary", "shore", "shoreline",
  "coast", "coastal", "aquifer", "groundwater", "flood", "flooding", "drought", "discharge",
  // water quality
  "water", "water quality", "quality", "pollution", "polluted", "contamination", "turbid",
  "turbidity", "sediment", "suspended", "erosion", "runoff", "nutrient", "nutrients",
  "nitrate", "nitrogen", "phosphorus", "eutrophic", "eutrophication", "hypoxia", "algae",
  "algal", "bloom", "blooms", "chlorophyll", "cyanobacteria", "clarity", "salinity",
  "hypersaline", "mineral", "silt", "ndci", "ndti", "index", "risk",
  // weather and climate
  "weather", "rain", "rainfall", "rainy", "storm", "stormwater", "precipitation",
  "temperature", "climate", "wind", "cloud", "clouds", "overcast", "monsoon", "season",
  "seasonal", "snow", "melt",
  // land and ecology
  "environment", "environmental", "ecology", "ecosystem", "habitat", "forest", "rainforest",
  "vegetation", "land cover", "farmland", "agriculture", "crop", "crops", "wildfire",
  "deforestation",
  // the dataset itself
  "ripple", "satellite", "sentinel", "remote sensing", "reflectance", "observation",
  "observations", "composite", "forecast", "outlook", "monitoring", "gauge", "gauges",
  "usgs", "nwis", "in-situ", "in situ", "validation", "validated", "accuracy", "map", "data",
  "dataset", "cell", "cells", "pixel", "resolution",
];

/** Entities that are not a body name but unambiguously mean one. */
export const BODY_ALIASES: Record<string, string> = {
  gsl: "great-salt-lake",
  "great salt": "great-salt-lake",
  erie: "lake-erie",
  superior: "lake-superior",
  michigan: "lake-michigan",
  huron: "lake-huron",
  ontario: "lake-ontario",
  tahoe: "lake-tahoe",
  okeechobee: "lake-okeechobee",
  winnipeg: "lake-winnipeg",
  winnebago: "lake-winnebago",
  salton: "salton-sea",
  champlain: "lake-champlain",
  mead: "lake-mead",
  powell: "lake-powell",
};

export interface ScopeVerdict {
  inScope: boolean;
  /** Terms that matched, for the UI to explain a refusal. */
  matched: string[];
}

/**
 * Is this question about the environment, or about this dataset?
 *
 * Deliberately generous: it accepts topics Ripple has no data for (a rainforest question
 * passes the gate and is then answered with "not in the data" by the grounding rules).
 * Refusing a legitimate question is worse than answering one with an honest "no data", and
 * the grounding rules — not this list — are what stop the model inventing an answer.
 */
export function checkScope(question: string): ScopeVerdict {
  const text = question.toLowerCase();
  const matched = ENVIRONMENT_TERMS.filter((term) => text.includes(term));
  if (matched.length > 0) return { inScope: true, matched };
  const alias = Object.keys(BODY_ALIASES).find((key) => text.includes(key));
  return { inScope: Boolean(alias), matched: alias ? [alias] : [] };
}

const ISO_DATE = /\d{4}-\d{2}-\d{2}/g;
/** Numbers as they are written: 4200, 1,234.5, 0.292. A trailing comma is punctuation. */
const NUMBER = /\d+(?:,\d{3})*(?:\.\d+)?/g;

/** Every number in a block of text, as floats, ignoring ISO dates. */
export function numbersIn(text: string): number[] {
  const stripped = text.replace(ISO_DATE, " ");
  const out: number[] = [];
  for (const match of stripped.match(NUMBER) ?? []) {
    const value = Number(match.replace(/,/g, ""));
    if (Number.isFinite(value)) out.push(value);
  }
  return out;
}

/**
 * The year, month and day of every ISO date in a block, as numbers.
 *
 * An answer that says "on Sep 17" is quoting a date, not inventing a measurement, and a
 * date's components are not otherwise present as numbers once the ISO form is stripped.
 * Without this the guard would flag every date an answer mentions.
 */
export function dateParts(text: string): number[] {
  const out: number[] = [];
  for (const match of text.match(ISO_DATE) ?? []) {
    const [year, month, day] = match.split("-").map(Number);
    out.push(year, month, day);
  }
  return out;
}

function decimalsOf(token: string): number {
  const dot = token.indexOf(".");
  return dot === -1 ? 0 : token.length - dot - 1;
}

/**
 * Is `value` an imprecise re-expression of `candidate` at `decimals` places?
 *
 * Both correct rounding and truncation count, because both are honest reductions of a
 * record: 0.4053 may be written 0.41 or 0.40, and a reader who checks either against the
 * record will find the same measurement. What does *not* count is a number that is neither,
 * at any precision — 0.42 is not a rounding of 0.4053 and is reported.
 */
function isReexpression(candidate: number, value: number, decimals: number): boolean {
  const scale = 10 ** decimals;
  const rounded = Math.round(candidate * scale) / scale;
  const truncated = Math.trunc(candidate * scale) / scale;
  return Math.abs(value - rounded) < 1e-9 || Math.abs(value - truncated) < 1e-9;
}

export interface NumberCheck {
  /** True when every number in the answer traces back to the sources or the question. */
  ok: boolean;
  /** Numbers as written in the answer that could not be traced back. */
  unverified: string[];
  checked: number;
}

/**
 * Every number in `answer` must be traceable to `sources` (or to the question itself).
 *
 * "Traceable" allows rounding and percent forms, because those are honest re-expressions:
 * a record of 0.4053 may be written 0.41 or 0.405, and a record of 0.292 may be written
 * 29% — but 0.87 or 31% are not re-expressions of anything and are reported as unverified.
 * A number the *user* wrote is allowed too: repeating a user's figure to say it is not in
 * the data is not an invention.
 */
export function verifyNumbers(answer: string, sources: string[], question = ""): NumberCheck {
  const sourceText = sources.join("\n");
  const known = [
    ...numbersIn(sourceText),
    ...dateParts(sourceText),
    ...numbersIn(question),
    ...dateParts(question),
  ];
  const stripped = answer.replace(ISO_DATE, " ");
  const tokens = stripped.match(NUMBER) ?? [];
  const unverified: string[] = [];

  for (const token of tokens) {
    const value = Number(token.replace(/,/g, ""));
    if (!Number.isFinite(value)) continue;
    const decimals = decimalsOf(token);
    const traced = known.some((candidate) =>
      isReexpression(candidate, value, decimals)
      || isReexpression(candidate * 100, value, decimals)
      || isReexpression(candidate / 100, value, decimals));
    if (!traced && !unverified.includes(token)) unverified.push(token);
  }

  return { ok: unverified.length === 0, unverified, checked: tokens.length };
}
