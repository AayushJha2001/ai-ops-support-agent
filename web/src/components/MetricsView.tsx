"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type MetricsSummary } from "@/lib/api";
import { isStaff, useSession } from "@/lib/session";
import { Card, ErrorNote, PageHeader } from "./ui";

const WINDOWS = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
];

export default function MetricsView() {
  const { persona } = useSession();
  const [hours, setHours] = useState(168);
  const [data, setData] = useState<MetricsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .metrics(persona!.apiKey, hours)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [persona, hours]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  if (!isStaff(persona)) return <ErrorNote message="Metrics are visible to support staff only." />;

  const r = data?.runs;
  const finished = r ? r.completed + r.escalated + r.blocked + r.errors : 0;
  const successRate = r && finished ? ((r.completed + r.escalated + r.blocked) / finished) * 100 : null;

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Agent metrics"
        subtitle="Every agent turn is traced: outcome, latency, LLM calls, tokens, cost and per-tool timings."
        action={
          <div className="flex rounded-lg bg-white/5 p-1 text-sm">
            {WINDOWS.map((w) => (
              <button
                key={w.hours}
                onClick={() => setHours(w.hours)}
                className={`rounded-md px-3 py-1 ${hours === w.hours ? "bg-white/10 text-white" : "text-slate-400"}`}
              >
                {w.label}
              </button>
            ))}
          </div>
        }
      />
      {error && <ErrorNote message={error} />}
      {r && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label="Agent turns" value={r.runs.toLocaleString()} />
            <Kpi
              label="Handled without error"
              value={successRate === null ? "-" : `${successRate.toFixed(0)}%`}
              hint="completed, escalated or safely blocked"
            />
            <Kpi label="p50 latency" value={fmtMs(r.p50_latency_ms)} />
            <Kpi label="p95 latency" value={fmtMs(r.p95_latency_ms)} />
            <Kpi label="Avg LLM calls / turn" value={Number(r.avg_llm_calls).toFixed(2)} />
            <Kpi label="Tokens in / out" value={`${compact(r.input_tokens)} / ${compact(r.output_tokens)}`} />
            <Kpi label="Est. cost" value={`$${Number(r.est_cost_usd).toFixed(4)}`} hint="free tier = $0" />
            <Kpi
              label="Approvals"
              value={`${data!.approvals.approved ?? 0} ✓ · ${data!.approvals.rejected ?? 0} ✗ · ${data!.approvals.pending ?? 0} ⏸`}
            />
          </div>

          <div className="mt-6 grid gap-4 lg:grid-cols-3">
            <Card className="p-5 lg:col-span-1">
              <h2 className="mb-4 text-sm font-semibold text-white">Outcomes</h2>
              {(
                [
                  ["completed", r.completed, "bg-emerald-400"],
                  ["awaiting approval", r.awaiting_approval, "bg-amber-400"],
                  ["escalated", r.escalated, "bg-fuchsia-400"],
                  ["blocked (guardrail)", r.blocked, "bg-rose-400"],
                  ["errors", r.errors, "bg-slate-400"],
                ] as const
              ).map(([label, n, color]) => (
                <div key={label} className="mb-3">
                  <div className="mb-1 flex justify-between text-xs text-slate-300">
                    <span>{label}</span>
                    <span>{n}</span>
                  </div>
                  <div className="h-2 rounded-full bg-white/5">
                    <div className={`h-2 rounded-full ${color}`} style={{ width: `${r.runs ? (n / r.runs) * 100 : 0}%` }} />
                  </div>
                </div>
              ))}
            </Card>

            <Card className="overflow-x-auto p-5 lg:col-span-2">
              <h2 className="mb-4 text-sm font-semibold text-white">Tool usage</h2>
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="pb-2">Tool</th>
                    <th className="pb-2 text-right">Calls</th>
                    <th className="pb-2 text-right">Failures</th>
                    <th className="pb-2 text-right">Avg time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {data!.tools.map((t) => (
                    <tr key={t.tool}>
                      <td className="py-2 font-mono text-xs text-indigo-300">{t.tool}</td>
                      <td className="py-2 text-right text-slate-200">{t.calls}</td>
                      <td className={`py-2 text-right ${t.failures ? "text-rose-300" : "text-slate-400"}`}>{t.failures}</td>
                      <td className="py-2 text-right text-slate-400">{fmtMs(t.avg_ms)}</td>
                    </tr>
                  ))}
                  {!data!.tools.length && (
                    <tr>
                      <td colSpan={4} className="py-6 text-center text-slate-500">No tool calls in this window.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-xl font-semibold text-white">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-slate-500">{hint}</div>}
    </Card>
  );
}

function fmtMs(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return "-";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function compact(n: number) {
  return Intl.NumberFormat("en", { notation: "compact" }).format(n);
}
