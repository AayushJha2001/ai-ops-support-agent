import type { ReactNode } from "react";

const STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  resolved: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  closed: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  approved: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  awaiting_approval: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  pending: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  escalated: "bg-fuchsia-500/15 text-fuchsia-300 ring-fuchsia-500/30",
  blocked: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  rejected: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  error: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  urgent: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  high: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  open: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  active: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
};

export function Badge({ value, className = "" }: { value: string; className?: string }) {
  const style = STATUS_STYLES[value] ?? "bg-slate-500/15 text-slate-300 ring-slate-500/30";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style} ${className}`}>
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-white/10 bg-white/[0.03] ${className}`}>{children}</div>;
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-white">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
      {message}
    </div>
  );
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "-";
  return value.replace(" UTC", "");
}
