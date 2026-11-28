"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, use, useState } from "react";
import { browser } from "react-dom";
import { isStaff, loadPersona, type Persona, PERSONAS, savePersona, SessionContext } from "@/lib/session";
import ApiStatus from "./ApiStatus";

const NAV = [
  { href: "/chat", label: "Chat", staffOnly: false, icon: "💬" },
  { href: "/approvals", label: "Approvals", staffOnly: true, icon: "✅" },
  { href: "/tickets", label: "Tickets", staffOnly: false, icon: "🎫" },
  { href: "/metrics", label: "Metrics", staffOnly: true, icon: "📈" },
];

export default function Shell({ children }: { children: ReactNode }) {
  use(browser("The signed-in demo persona is stored in localStorage."));
  const [persona, setPersonaState] = useState<Persona | null>(() => loadPersona());
  const pathname = usePathname();

  const setPersona = (p: Persona | null) => {
    savePersona(p);
    setPersonaState(p);
  };

  if (!persona) return <Login onPick={setPersona} />;

  return (
    <SessionContext.Provider value={{ persona, setPersona }}>
      <div className="flex min-h-screen">
        <aside className="hidden w-60 shrink-0 flex-col border-r border-white/10 bg-slate-950/60 px-4 py-6 md:flex">
          <div className="mb-8 px-2">
            <div className="text-sm font-semibold text-white">Acme Cloud</div>
            <div className="text-xs text-slate-400">AI Ops Console</div>
          </div>
          <nav className="flex flex-col gap-1">
            {NAV.filter((n) => !n.staffOnly || isStaff(persona)).map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${
                  pathname.startsWith(n.href)
                    ? "bg-indigo-500/15 text-white ring-1 ring-inset ring-indigo-400/30"
                    : "text-slate-300 hover:bg-white/5"
                }`}
              >
                <span aria-hidden>{n.icon}</span>
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="mt-auto rounded-lg border border-white/10 p-3">
            <div className="text-xs text-slate-400">Signed in as</div>
            <div className="truncate text-sm font-medium text-white">{persona.name}</div>
            <div className="text-xs capitalize text-slate-400">{persona.role.replace("_", " ")}</div>
            <button
              onClick={() => setPersona(null)}
              className="mt-3 w-full rounded-md bg-white/5 px-2 py-1.5 text-xs text-slate-200 hover:bg-white/10"
            >
              Switch persona
            </button>
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center justify-between border-b border-white/10 px-4 py-3 md:hidden">
            <span className="text-sm font-semibold text-white">AI Ops Console</span>
            <div className="flex gap-3 text-sm">
              {NAV.filter((n) => !n.staffOnly || isStaff(persona)).map((n) => (
                <Link key={n.href} href={n.href} className="text-slate-300">
                  {n.label}
                </Link>
              ))}
            </div>
          </header>
          <main className="flex-1 px-4 py-6 md:px-8">{children}</main>
        </div>
      </div>
    </SessionContext.Provider>
  );
}

function Login({ onPick }: { onPick: (p: Persona) => void }) {
  return (
    <div className="mx-auto flex min-h-screen max-w-4xl flex-col justify-center px-4 py-12">
      <div className="mb-10">
        <div className="mb-3 inline-flex rounded-full bg-indigo-500/15 px-3 py-1 text-xs text-indigo-300 ring-1 ring-inset ring-indigo-400/30">
          LangGraph · FastAPI · PostgreSQL/pgvector
        </div>
        <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">AI Operations &amp; Support Agent</h1>
        <p className="mt-3 max-w-2xl text-slate-400">
          A support agent that looks things up, opens tickets and issues refunds - with every risky action paused for a
          human to approve. Pick a demo persona to start.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <ApiStatus />
          <a
            href="https://github.com/AayushJha2001/ai-ops-support-agent"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-slate-400 underline-offset-4 hover:text-white hover:underline"
          >
            Source on GitHub ↗
          </a>
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Demo data is fictional and resets periodically. Try David&apos;s refund request, then switch to Sam to approve it.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {PERSONAS.map((p) => (
          <button
            key={p.id}
            onClick={() => onPick(p)}
            className="group rounded-xl border border-white/10 bg-white/[0.03] p-4 text-left transition hover:border-indigo-400/50 hover:bg-indigo-500/5"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium text-white">{p.name}</span>
              <span className="rounded-full bg-white/5 px-2 py-0.5 text-xs capitalize text-slate-300">
                {p.role.replace("_", " ")}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-400">{p.blurb}</p>
          </button>
        ))}
      </div>
    </div>
  );
}
