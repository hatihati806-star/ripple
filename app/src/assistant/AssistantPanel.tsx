import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CLIENT_ACTIONS, type AssistantAction } from "./actions";
import type { AssistantAnswer, Source } from "./types";

interface AssistantPanelProps {
  /** Opens the assistant's source records, so a reader can audit what it was given. */
  factsUrl: string;
  /**
   * Runs an action the model asked for. The panel owns the chip's state, the app owns the
   * effect: only the app can move the map, step the timeline or open the 3D view.
   */
  onAction: (action: AssistantAction) => Promise<void> | void;
  onClose: () => void;
}

interface Chip {
  key: string;
  action: AssistantAction;
  state: "running" | "done";
}

interface Turn {
  role: "user" | "assistant";
  text: string;
  chips?: Chip[];
  sources?: Source[];
  unverified?: string[];
  refused?: AssistantAnswer["refused"];
  model?: string;
}

const QUICK_ACTIONS = [
  { label: "Worst lake now", question: "Which lake looks worst right now?" },
  { label: "Next 7 days", question: "What does the next 7 days look like for the worst lake?" },
  { label: "Show it in 3D", question: "Show me the worst lake in 3D." },
];

const KIND_STYLE: Record<Source["kind"], string> = {
  body: "bg-sky-50 text-sky-800",
  frame: "bg-slate-100 text-slate-600",
  method: "bg-violet-50 text-violet-800",
  validation: "bg-emerald-50 text-emerald-800",
  dataset: "bg-slate-100 text-slate-600",
};

/** The sparkle in the header: the assistant's mark, drawn rather than borrowed. */
function Sparkle({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className} fill="currentColor">
      <path d="M12 2.2c.5 0 .9.3 1 .8l1.2 4.6c.2.7.7 1.2 1.4 1.4l4.6 1.2c.5.1.8.5.8 1s-.3.9-.8 1l-4.6 1.2c-.7.2-1.2.7-1.4 1.4l-1.2 4.6c-.1.5-.5.8-1 .8s-.9-.3-1-.8l-1.2-4.6c-.2-.7-.7-1.2-1.4-1.4L3.8 12.2c-.5-.1-.8-.5-.8-1s.3-.9.8-1l4.6-1.2c.7-.2 1.2-.7 1.4-1.4L11 3c.1-.5.5-.8 1-.8Z" />
    </svg>
  );
}

function SendArrow() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4" fill="none"
         stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 19V5" />
      <path d="m5.5 11.5 6.5-6.5 6.5 6.5" />
    </svg>
  );
}

/**
 * The action chip: a spinner while the assistant works, a green tick when it is done.
 *
 * The chip is the honest part of the agent: it says what actually happened, and it only
 * reaches "done" when the app reports the effect ran.
 */
function ActionChip({ chip }: { chip: Chip }) {
  return (
    <span
      className="animate-chip-in inline-flex items-center gap-1.5 rounded-full border border-slate-200/80 bg-white/90 px-2.5 py-1 text-[11px] font-medium text-slate-600 shadow-sm"
      role="status"
    >
      {chip.state === "running" ? (
        <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
      ) : (
        <span
          className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-white"
          aria-hidden="true"
        >
          <svg viewBox="0 0 24 24" className="h-2.5 w-2.5" fill="none" stroke="currentColor"
               strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="m5 13 4 4L19 7" />
          </svg>
        </span>
      )}
      {chip.state === "running" ? chip.action.running : chip.action.done}
    </span>
  );
}

/**
 * The assistant, grounded in the shipped dataset and able to act on the map.
 *
 * Every answer carries the records it was built from; any figure the endpoint could not trace
 * back to those records is shown as unverified rather than rendered as fact; and each action
 * it took is a chip that only turns green once the map has actually done it.
 */
export function AssistantPanel({ factsUrl, onAction, onClose }: AssistantPanelProps) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const sequence = useRef(0);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, busy]);

  /** Flip a chip from running to done, by key, inside the turn that owns it. */
  const finishChip = useCallback((turnIndex: number, key: string) => {
    setTurns((previous) =>
      previous.map((turn, index) =>
        index === turnIndex && turn.chips
          ? {
              ...turn,
              chips: turn.chips.map((chip) =>
                chip.key === key ? { ...chip, state: "done" } : chip,
              ),
            }
          : turn,
      ),
    );
  }, []);

  const ask = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || busy) return;
      setInput("");
      setBusy(true);

      const turnIndex = turns.length + 1; // +1: the user turn is appended next
      setTurns((previous) => [...previous, { role: "user", text: trimmed }]);

      try {
        const response = await fetch("/api/assistant", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: trimmed }),
        });
        const payload = (await response.json()) as AssistantAnswer & { error?: string };

        if (response.status === 501) {
          setUnavailable(payload.error ?? "The assistant is not configured here.");
          setTurns((previous) => previous.slice(0, -1));
          return;
        }
        if (!response.ok) {
          setTurns((previous) => [
            ...previous,
            {
              role: "assistant",
              text: payload.error ??
                "The assistant could not answer that. The rest of the map is unaffected.",
              refused: "error",
            },
          ]);
          return;
        }

        // Show the actions immediately, then run them. UI actions start as spinners and the
        // map moves while the answer is still being read.
        const actions = payload.actions ?? [];
        const chips: Chip[] = actions.map((action) => ({
          key: `a${(sequence.current += 1)}`,
          action,
          state: CLIENT_ACTIONS.has(action.kind) ? "running" : "done",
        }));
        setTurns((previous) => [
          ...previous,
          {
            role: "assistant",
            text: payload.answer,
            chips,
            sources: payload.sources,
            unverified: payload.unverified,
            refused: payload.refused,
            model: payload.model,
          },
        ]);

        for (const chip of chips) {
          if (chip.state !== "running") continue;
          try {
            await onAction(chip.action);
          } catch {
            // A failed action must not read as a successful one.
          }
          finishChip(turnIndex, chip.key);
        }
      } catch {
        setTurns((previous) => [
          ...previous,
          {
            role: "assistant",
            text: "The assistant is unreachable from here. The map and the data are unaffected.",
            refused: "error",
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [busy, finishChip, onAction, turns.length],
  );

  const suggestions = useMemo(() => QUICK_ACTIONS, []);

  return (
    <section
      className="animate-panel-in pointer-events-auto flex h-full w-full flex-col overflow-hidden rounded-2xl bg-white shadow-[0_18px_50px_-18px_rgba(15,23,42,0.45)] ring-1 ring-slate-900/5"
      aria-label="AI assistant"
    >
      <header className="flex items-start justify-between gap-2 border-b border-slate-100 px-4 py-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <Sparkle className="mt-0.5 h-5 w-5 shrink-0 text-orange-500" />
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold leading-tight tracking-tight text-slate-900">
              Ask Ripple
            </h2>
            <p className="text-[11px] leading-snug text-slate-500">
              DeepSeek · answers only from Ripple&apos;s data
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Hide assistant"
          className="-mr-1 -mt-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
        >
          <span aria-hidden="true">✕</span>
        </button>
      </header>

      <div
        ref={scrollRef}
        className="panel-scroll flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-3"
        aria-live="polite"
      >
        {turns.length === 0 && !unavailable && (
          <p className="text-[12px] leading-relaxed text-slate-500">
            Ask about a lake on the map, the indices behind the colours, the 7-day outlook, or
            how the data was validated against USGS gauges. It can move the map for you.
          </p>
        )}

        {unavailable && (
          <p className="rounded-xl bg-amber-50 px-3 py-2 text-[12px] leading-snug text-amber-800">
            {unavailable} The map, the ranking and the forecasts all work without it.
          </p>
        )}

        {turns.map((turn, index) =>
          turn.role === "user" ? (
            <div key={index} className="animate-bubble-in flex justify-end">
              <p className="max-w-[85%] rounded-2xl rounded-br-md bg-slate-900 px-3.5 py-2 text-[12.5px] leading-snug text-white shadow-sm">
                {turn.text}
              </p>
            </div>
          ) : (
            <div key={index} className="animate-bubble-in space-y-2">
              {turn.chips && turn.chips.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {turn.chips.map((chip) => (
                    <ActionChip key={chip.key} chip={chip} />
                  ))}
                </div>
              )}
              <p className="border-l-2 border-orange-400 pl-3 text-[12.5px] leading-relaxed text-slate-700">
                {turn.text}
              </p>
              {turn.sources && turn.sources.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {turn.sources.slice(0, 6).map((source) => (
                    <span
                      key={`${source.kind}-${source.label}`}
                      className={`rounded px-1.5 py-0.5 text-[9.5px] font-medium ${KIND_STYLE[source.kind]}`}
                      title={`record used: ${source.kind}`}
                    >
                      {source.label}
                    </span>
                  ))}
                </div>
              )}
              {turn.refused === "out-of-scope" && (
                <p className="text-[10px] text-slate-400">
                  Refused without calling the model: the question was outside this dataset&apos;s
                  remit.
                </p>
              )}
            </div>
          ),
        )}

        {busy && (
          <span className="animate-chip-in inline-flex w-fit items-center gap-1.5 rounded-full border border-slate-200/80 bg-white/90 px-2.5 py-1 text-[11px] font-medium text-slate-600 shadow-sm">
            <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
            Reading the shipped records…
          </span>
        )}
      </div>

      <div className="border-t border-slate-100 px-4 pb-3 pt-2.5">
        <div className="flex flex-wrap gap-1.5 pb-2.5">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion.label}
              type="button"
              onClick={() => void ask(suggestion.question)}
              disabled={busy}
              className="min-h-9 rounded-full border border-orange-200 bg-orange-50/60 px-3 text-[11.5px] font-medium text-orange-700 transition hover:border-orange-300 hover:bg-orange-50 disabled:opacity-50"
            >
              {suggestion.label}
            </button>
          ))}
        </div>
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void ask(input);
          }}
        >
          <input
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            maxLength={500}
            placeholder="Ask about any lake…"
            aria-label="Question for the assistant"
            className="min-h-11 min-w-0 flex-1 rounded-full border border-slate-200 bg-white px-4 text-[12.5px] text-slate-700 outline-none placeholder:text-slate-400 focus:border-slate-300"
          />
          <button
            type="submit"
            disabled={busy || input.trim().length === 0}
            aria-label="Send question"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-slate-900 text-white transition hover:bg-slate-700 disabled:opacity-35"
          >
            <SendArrow />
          </button>
        </form>
        <p className="pt-2 text-[9.5px] leading-snug text-slate-400">
          Optical proxies, never toxicity or safety advice · figures are checked against the
          records, untraceable ones are labelled ·{" "}
          <a className="underline decoration-dotted" href={factsUrl} target="_blank" rel="noreferrer">
            read the exact records
          </a>
        </p>
      </div>
    </section>
  );
}
