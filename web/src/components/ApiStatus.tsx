"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

type State = { kind: "checking" } | { kind: "waking"; seconds: number } | { kind: "ready"; provider: string } | { kind: "down" };

/** Pings /health until the API answers. Free hosting sleeps when idle, so the first visit can take ~1 min. */
export default function ApiStatus() {
  const [state, setState] = useState<State>({ kind: "checking" });

  useEffect(() => {
    let alive = true;
    const started = Date.now();
    async function ping() {
      try {
        const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
        if (res.ok) {
          const body = await res.json();
          if (alive) setState({ kind: "ready", provider: body.llm_provider });
          return;
        }
      } catch {
        /* still asleep */
      }
      if (!alive) return;
      const seconds = Math.round((Date.now() - started) / 1000);
      setState(seconds > 150 ? { kind: "down" } : { kind: "waking", seconds });
      if (seconds <= 150) setTimeout(ping, 3000);
    }
    ping();
    return () => {
      alive = false;
    };
  }, []);

  if (state.kind === "ready")
    return (
      <div className="inline-flex items-center gap-2 rounded-full bg-emerald-500/10 px-3 py-1 text-xs text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> API online · LLM: {state.provider}
      </div>
    );
  if (state.kind === "down")
    return (
      <div className="inline-flex rounded-full bg-rose-500/10 px-3 py-1 text-xs text-rose-300 ring-1 ring-inset ring-rose-500/30">
        API is unreachable right now. Please try again in a few minutes.
      </div>
    );
  return (
    <div className="inline-flex items-center gap-2 rounded-full bg-amber-500/10 px-3 py-1 text-xs text-amber-300 ring-1 ring-inset ring-amber-500/30">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
      {state.kind === "checking"
        ? "Connecting to the API…"
        : `Waking up the free-tier server… ${state.seconds}s (first visit can take ~1 min)`}
    </div>
  );
}
