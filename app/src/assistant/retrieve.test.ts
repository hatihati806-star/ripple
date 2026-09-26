import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { MAX_CONTEXT_CHARS, retrieve, scoreBody } from "./retrieve";
import type { FactBase } from "./types";

/**
 * Retrieval is tested against the *shipped* fact base, not a fixture: what matters is that
 * the records the assistant is handed for a real question are the right ones, with the real
 * numbers in them.
 */
const here = dirname(fileURLToPath(import.meta.url));
const facts = JSON.parse(
  readFileSync(resolve(here, "../../public/data/assistant.json"), "utf-8"),
) as FactBase;

describe("the shipped fact base", () => {
  it("carries every catalogued body with its readings", () => {
    expect(facts.dataset.body_count).toBe(250);
    expect(facts.bodies).toHaveLength(250);
    expect(facts.dataset.bodies_with_a_reading).toBe(250);
    for (const body of facts.bodies) {
      expect(body.latest).not.toBeNull();
      expect(typeof body.latest?.risk).toBe("number");
    }
  });

  it("carries the method, the limits and the validation summary", () => {
    expect(facts.method.limits.length).toBeGreaterThan(5);
    expect(facts.method.disclaimer).toMatch(/not a regulatory measurement/i);
    expect(facts.validation?.pairs).toBe(135);
    expect(facts.validation?.gauges).toBe(25);
    expect(facts.validation?.published_cell_value.spearman_rho).toBeCloseTo(0.292, 3);
    expect(facts.validation?.water_only.spearman_rho).toBeCloseTo(0.463, 3);
  });

  it("keeps the Great Salt Lake caution that the index needs there", () => {
    const salt = facts.bodies.find((body) => body.id === "great-salt-lake");
    expect(salt?.caution).toMatch(/hypersaline/i);
  });
});

describe("scoreBody", () => {
  const erie = facts.bodies.find((body) => body.id === "lake-erie")!;

  it("scores an exact name highest", () => {
    expect(scoreBody(erie, "what about lake erie?", ["what", "about", "lake", "erie"]))
      .toBe(100);
  });

  it("scores a distinctive token", () => {
    expect(scoreBody(erie, "how is erie doing?", ["how", "is", "erie", "doing"]))
      .toBeGreaterThan(0);
  });

  it("does not match on a generic word", () => {
    const generic = facts.bodies.find((body) => body.name.startsWith("Water body"))!;
    expect(scoreBody(generic, "how is the lake?", ["how", "is", "the", "lake"])).toBe(0);
  });
});

describe("retrieve", () => {
  it("pulls the body a question names, with its numbers", () => {
    const result = retrieve(facts, "How did Lake Erie change this month?");
    expect(result.matchedBodies).toContain("Lake Erie");
    expect(result.context).toContain("BODY Lake Erie");
    expect(result.context).toContain("0.4053");
    expect(result.sources.some((source) => source.kind === "body")).toBe(true);
  });

  it("pulls the validation block only when the question is about accuracy", () => {
    const plain = retrieve(facts, "How is Lake Erie?");
    expect(plain.context).not.toContain("VALIDATION");
    const aboutAccuracy = retrieve(facts, "How accurate is the turbidity index?");
    expect(aboutAccuracy.context).toContain("VALIDATION");
    expect(aboutAccuracy.context).toContain("0.292");
    expect(aboutAccuracy.sources.some((source) => source.kind === "validation")).toBe(true);
  });

  it("pulls the ranking for a worst/cleanest question", () => {
    const result = retrieve(facts, "Which lake is worst right now?");
    expect(result.context).toContain("RANKING");
    expect(result.context).toMatch(/highest:/);
    expect(result.context).toMatch(/lowest:/);
    // Explicit ranks, so the order cannot be misread.
    expect(result.context).toMatch(/\n {2}1\. /);
    expect(result.context).toMatch(/\n {2}2\. /);
  });

  it("always carries the dataset block and, when a body is named, its limits", () => {
    const result = retrieve(facts, "How much rain is forecast for Lake Okeechobee?");
    expect(result.context).toContain("DATASET");
    expect(result.context).toContain("METHOD AND LIMITS");
    expect(result.context).toContain("BODY Lake Okeechobee");
    expect(result.context).toContain("forecast daily runoff mm");
  });

  it("resolves an alias to the body", () => {
    const result = retrieve(facts, "Is gsl getting worse?");
    expect(result.matchedBodies).toContain("Great Salt Lake");
    expect(result.context).toMatch(/hypersaline/i);
  });

  it("caps how many bodies it will pull in", () => {
    const result = retrieve(
      facts,
      "Compare Lake Erie, Lake Superior, Lake Michigan, Lake Huron, Lake Ontario and "
        + "Lake Tahoe",
    );
    expect(result.matchedBodies.length).toBeLessThanOrEqual(4);
  });

  it("still answers a dataset-level question with no body named", () => {
    const result = retrieve(facts, "How many water bodies are in the dataset?");
    expect(result.matchedBodies).toEqual([]);
    expect(result.context).toContain("DATASET");
    expect(result.context).toContain("250 bodies catalogued");
  });

  it("keeps the context inside the model's budget", () => {
    for (const question of [
      "Which lake is worst right now?",
      "How accurate is the turbidity index?",
      "How is the per-body reading measured?",
      "What will happen to Lake Okeechobee next week?",
    ]) {
      expect(retrieve(facts, question).context.length).toBeLessThanOrEqual(
        MAX_CONTEXT_CHARS + 40,
      );
    }
  });

  it("is deterministic", () => {
    const first = retrieve(facts, "How did Lake Erie change this month?");
    const second = retrieve(facts, "How did Lake Erie change this month?");
    expect(second.context).toBe(first.context);
    expect(second.sources).toEqual(first.sources);
  });
});
