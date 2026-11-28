"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, chatStream, type HistoryMessage, type Thread, type TurnResult } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Badge, ErrorNote } from "./ui";

type LiveTool = { name: string; args?: Record<string, unknown>; status?: string; preview?: string };

export default function ChatView() {
  const { persona } = useSession();
  const key = persona!.apiKey;

  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveText, setLiveText] = useState("");
  const [liveTools, setLiveTools] = useState<LiveTool[]>([]);
  const [pendingHuman, setPendingHuman] = useState<string | null>(null);
  const [last, setLast] = useState<TurnResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const activeThread = threads.find((t) => t.thread_id === threadId);
  const status = last?.thread_id === threadId ? last.status : activeThread?.status;

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await api.threads(key));
    } catch (e) {
      setError(String((e as Error).message));
    }
  }, [key]);

  const loadThread = useCallback(
    async (id: string) => {
      const t = await api.thread(key, id);
      setHistory(t.messages);
    },
    [key],
  );

  useEffect(() => {
    // The view remounts when the persona changes (see Shell), so only the thread list needs loading.
    let alive = true;
    api
      .threads(key)
      .then((t) => alive && setThreads(t))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [key]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, liveText, liveTools, pendingHuman]);

  // While waiting for a human approval, poll until the run resumes.
  useEffect(() => {
    if (status !== "awaiting_approval" || !threadId) return;
    const timer = setInterval(async () => {
      const list = await api.threads(key).catch(() => null);
      const t = list?.find((x) => x.thread_id === threadId);
      if (list && t && t.status !== "awaiting_approval") {
        setThreads(list);
        setLast(null);
        await loadThread(threadId);
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [status, threadId, key, loadThread]);

  async function openThread(id: string) {
    setThreadId(id);
    setLast(null);
    setError(null);
    await loadThread(id);
  }

  function newThread() {
    setThreadId(null);
    setHistory([]);
    setLast(null);
    setError(null);
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setError(null);
    setLiveText("");
    setLiveTools([]);
    setPendingHuman(message);
    let currentThread = threadId;
    try {
      for await (const ev of chatStream(key, message, threadId)) {
        if (ev.event === "thread") {
          currentThread = ev.data.thread_id;
          setThreadId(currentThread);
        } else if (ev.event === "token") {
          setLiveText((t) => t + ev.data.text);
        } else if (ev.event === "tool_calls") {
          setLiveText("");
          setLiveTools((tools) => [...tools, ...ev.data.map((c) => ({ name: c.name, args: c.args }))]);
        } else if (ev.event === "tool_result") {
          setLiveTools((tools) => {
            const copy = [...tools];
            const idx = copy.findIndex((t) => t.name === ev.data.name && !t.status);
            if (idx >= 0) copy[idx] = { ...copy[idx], status: ev.data.status, preview: ev.data.preview };
            return copy;
          });
        } else if (ev.event === "done") {
          setLast(ev.data);
        }
      }
      if (currentThread) await loadThread(currentThread);
      await refreshThreads();
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
      setPendingHuman(null);
      setLiveText("");
      setLiveTools([]);
    }
  }

  const showEmpty = !history.length && !pendingHuman;

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-6">
      {/* thread list */}
      <aside className="hidden w-64 shrink-0 flex-col lg:flex">
        <button
          onClick={newThread}
          className="mb-3 rounded-lg bg-indigo-500 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-400"
        >
          + New conversation
        </button>
        <div className="flex-1 space-y-1 overflow-y-auto">
          {threads.map((t) => (
            <button
              key={t.thread_id}
              onClick={() => openThread(t.thread_id)}
              className={`w-full rounded-lg px-3 py-2 text-left text-sm transition ${
                t.thread_id === threadId ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5"
              }`}
            >
              <div className="truncate">{t.title || "Conversation"}</div>
              <div className="mt-1">
                <Badge value={t.status} />
              </div>
            </button>
          ))}
          {!threads.length && <p className="px-3 text-xs text-slate-500">No conversations yet.</p>}
        </div>
      </aside>

      {/* conversation */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-white">{activeThread?.title ?? "New conversation"}</h1>
            <p className="text-xs text-slate-400">Chatting as {persona!.name}</p>
          </div>
          {status && <Badge value={status} />}
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto rounded-xl border border-white/10 bg-white/[0.02] p-4">
          {showEmpty && <Suggestions prompts={persona!.prompts} onPick={send} />}
          {history.map((m, i) => (
            <MessageItem key={i} m={m} />
          ))}
          {pendingHuman && <Bubble role="human" text={pendingHuman} />}
          {liveTools.map((t, i) => (
            <ToolChip key={`live-${i}`} name={t.name} args={t.args} status={t.status ?? "running"} preview={t.preview} />
          ))}
          {busy && (liveText ? <Bubble role="ai" text={liveText} /> : <Thinking />)}
          {status === "awaiting_approval" && !busy && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              ⏸ Waiting for a support specialist to approve this action. The conversation resumes automatically once
              it is reviewed (open <b>Approvals</b> as Sam or Admin).
            </div>
          )}
          {error && <ErrorNote message={error} />}
          <div ref={bottomRef} />
        </div>

        {last && last.thread_id === threadId && <TurnFooter result={last} />}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
          className="mt-3 flex gap-2"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy || status === "awaiting_approval"}
            placeholder={status === "awaiting_approval" ? "Waiting for approval…" : "Ask the support agent…"}
            className="flex-1 rounded-lg border border-white/10 bg-white/5 px-4 py-2.5 text-sm text-white placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={busy || !input.trim() || status === "awaiting_approval"}
            className="rounded-lg bg-indigo-500 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-40"
          >
            {busy ? "…" : "Send"}
          </button>
        </form>
      </section>
    </div>
  );
}

function Suggestions({ prompts, onPick }: { prompts: string[]; onPick: (p: string) => void }) {
  return (
    <div className="py-6 text-center">
      <p className="mb-4 text-sm text-slate-400">Try one of these:</p>
      <div className="mx-auto flex max-w-2xl flex-col gap-2">
        {prompts.map((p) => (
          <button
            key={p}
            onClick={() => onPick(p)}
            className="rounded-lg border border-white/10 bg-white/[0.03] px-4 py-2.5 text-left text-sm text-slate-200 hover:border-indigo-400/50"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageItem({ m }: { m: HistoryMessage }) {
  if (m.role === "human") return <Bubble role="human" text={m.content} />;
  if (m.role === "tool") return <ToolChip name={m.name ?? "tool"} status={m.status ?? "success"} preview={m.content} done />;
  if (m.role === "ai") {
    return (
      <>
        {m.tool_calls?.map((c, i) => <ToolCallLine key={i} name={c.name} args={c.args} />)}
        {m.content && <Bubble role="ai" text={m.content} />}
      </>
    );
  }
  return null;
}

function Bubble({ role, text }: { role: "human" | "ai"; text: string }) {
  const mine = role === "human";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          mine ? "rounded-br-sm bg-indigo-500 text-white" : "rounded-bl-sm bg-white/[0.06] text-slate-100"
        }`}
      >
        {text}
      </div>
    </div>
  );
}

function ToolCallLine({ name, args }: { name: string; args: Record<string, unknown> }) {
  return (
    <div className="font-mono text-xs text-slate-400">
      <span className="text-indigo-300">→ {name}</span>({shortArgs(args)})
    </div>
  );
}

function ToolChip({
  name,
  args,
  status,
  preview,
  done,
}: {
  name: string;
  args?: Record<string, unknown>;
  status: string;
  preview?: string;
  done?: boolean;
}) {
  const ok = status === "success" || status === "running";
  const icon = status === "running" ? "⏳" : status === "error" ? "✗" : "✓";
  return (
    <details className="group text-xs">
      <summary
        className={`inline-flex cursor-pointer list-none items-center gap-2 rounded-md px-2 py-1 font-mono ${
          ok ? "bg-emerald-500/10 text-emerald-300" : "bg-rose-500/10 text-rose-300"
        }`}
      >
        <span>{icon}</span>
        {done ? "result" : "tool"}: {name}
        {args && <span className="text-slate-400">({shortArgs(args)})</span>}
      </summary>
      {preview && (
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-black/40 p-2 text-[11px] text-slate-300">
          {preview}
        </pre>
      )}
    </details>
  );
}

function Thinking() {
  return (
    <div className="flex items-center gap-2 text-xs text-slate-400">
      <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
      Agent is working…
    </div>
  );
}

function TurnFooter({ result }: { result: TurnResult }) {
  const m = result.metrics;
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
      <span>⏱ {(m.latency_ms / 1000).toFixed(1)}s</span>
      <span>🧠 {m.llm_calls} LLM calls</span>
      <span>
        🔤 {m.input_tokens.toLocaleString()} in / {m.output_tokens.toLocaleString()} out
      </span>
      <span>💲{m.est_cost_usd.toFixed(4)}</span>
      {m.models.length > 0 && <span>🤖 {m.models.join(", ")}</span>}
      {result.flags.length > 0 && <span>🛡 {result.flags.join(", ")}</span>}
    </div>
  );
}

function shortArgs(args: Record<string, unknown>) {
  const s = Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? JSON.stringify(v) : JSON.stringify(v)}`)
    .join(", ");
  return s.length > 90 ? s.slice(0, 90) + "…" : s;
}
