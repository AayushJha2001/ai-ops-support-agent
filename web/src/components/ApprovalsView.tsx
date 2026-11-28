"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type ApprovalRow, type TurnResult } from "@/lib/api";
import { isStaff, useSession } from "@/lib/session";
import { Badge, Card, ErrorNote, formatDate, PageHeader } from "./ui";

export default function ApprovalsView() {
  const { persona } = useSession();
  const key = persona!.apiKey;
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [working, setWorking] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, TurnResult>>({});
  const [justReviewed, setJustReviewed] = useState<{ row: ApprovalRow; result: TurnResult }[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.approvals(key, filter));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [key, filter]);

  useEffect(() => {
    let alive = true;
    const fetchRows = () =>
      api
        .approvals(key, filter)
        .then((r) => {
          if (!alive) return;
          setRows(r);
          setError(null);
        })
        .catch((e: Error) => alive && setError(e.message));
    fetchRows();
    const t = setInterval(fetchRows, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [key, filter]);

  if (!isStaff(persona)) return <ErrorNote message="Approvals are visible to support staff only." />;

  async function decide(row: ApprovalRow, decision: "approve" | "reject") {
    setWorking(row.id);
    setError(null);
    try {
      const result = await api.decide(key, row.id, decision, notes[row.id]);
      setResults((r) => ({ ...r, [row.id]: result }));
      setJustReviewed((list) => [
        {
          row: { ...row, status: decision === "approve" ? "approved" : "rejected", reviewed_by: persona!.name, review_note: notes[row.id] ?? null },
          result,
        },
        ...list,
      ]);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Approvals"
        subtitle="Write actions the agent paused for a human decision. Approving resumes the agent run immediately."
        action={
          <div className="flex rounded-lg bg-white/5 p-1 text-sm">
            {(["pending", "all"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded-md px-3 py-1 capitalize ${filter === f ? "bg-white/10 text-white" : "text-slate-400"}`}
              >
                {f}
              </button>
            ))}
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorNote message={error} /></div>}

      {filter === "pending" && justReviewed.length > 0 && (
        <div className="mb-6 space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Just reviewed</h2>
          {justReviewed.map(({ row, result }) => (
            <Card key={`done-${row.id}`} className="border-emerald-500/20 p-4">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-semibold text-white">#{row.id}</span>
                <Badge value={row.status} />
                <span className="font-mono text-xs text-indigo-300">{row.tool_calls.map((c) => c.name).join(", ")}</span>
                <span className="text-xs text-slate-500">· agent resumed</span>
                <Badge value={result.status} />
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm text-slate-200">{result.reply}</p>
            </Card>
          ))}
        </div>
      )}

      <div className="space-y-4">
        {rows.map((row) => (
          <Card key={row.id} className="p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-white">#{row.id}</span>
                  <Badge value={row.status} />
                </div>
                <p className="mt-1 text-sm text-slate-300">“{row.thread_title}”</p>
                <p className="mt-1 text-xs text-slate-500">
                  Requested by <b className="text-slate-300">{row.requested_by}</b> · {formatDate(row.created_at)}
                  {row.reviewed_by && (
                    <>
                      {" "}· reviewed by <b className="text-slate-300">{row.reviewed_by}</b>
                      {row.review_note && <> (“{row.review_note}”)</>}
                    </>
                  )}
                </p>
              </div>
            </div>

            <div className="mt-4 space-y-2">
              {row.tool_calls.map((c) => (
                <div key={c.id} className="rounded-lg bg-black/30 p-3 font-mono text-xs">
                  <div className="text-indigo-300">{c.name}</div>
                  <pre className="mt-1 whitespace-pre-wrap text-slate-300">{JSON.stringify(c.args, null, 2)}</pre>
                </div>
              ))}
              {row.risk_reasons.map((r, i) => (
                <p key={i} className="text-xs text-amber-300">⚠ {r}</p>
              ))}
            </div>

            {row.status === "pending" && (
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <input
                  value={notes[row.id] ?? ""}
                  onChange={(e) => setNotes((n) => ({ ...n, [row.id]: e.target.value }))}
                  placeholder="Optional note (shown to the agent on reject)"
                  className="min-w-56 flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none"
                />
                <button
                  onClick={() => decide(row, "approve")}
                  disabled={working !== null}
                  className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-400 disabled:opacity-40"
                >
                  {working === row.id ? "Working…" : "Approve"}
                </button>
                <button
                  onClick={() => decide(row, "reject")}
                  disabled={working !== null}
                  className="rounded-lg bg-rose-500/80 px-4 py-2 text-sm font-medium text-white hover:bg-rose-500 disabled:opacity-40"
                >
                  Reject
                </button>
              </div>
            )}

            {results[row.id] && (
              <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.03] p-3 text-sm">
                <div className="mb-1 flex items-center gap-2 text-xs text-slate-400">
                  Agent resumed <Badge value={results[row.id].status} />
                </div>
                <p className="whitespace-pre-wrap text-slate-200">{results[row.id].reply}</p>
              </div>
            )}
          </Card>
        ))}
        {!rows.length && (
          <Card className="p-10 text-center text-sm text-slate-400">
            {filter === "pending" ? "Nothing waiting for review. 🎉" : "No approvals yet."}
          </Card>
        )}
      </div>
    </div>
  );
}
