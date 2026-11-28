"use client";

import { useEffect, useState } from "react";
import { api, type Ticket } from "@/lib/api";
import { isStaff, useSession } from "@/lib/session";
import { Badge, Card, ErrorNote, formatDate, PageHeader } from "./ui";

export default function TicketsView() {
  const { persona } = useSession();
  const [rows, setRows] = useState<Ticket[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .tickets(persona!.apiKey)
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }, [persona]);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Tickets"
        subtitle={isStaff(persona) ? "All customer tickets" : "Your support tickets"}
      />
      {error && <ErrorNote message={error} />}
      <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-white/10 text-left text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">#</th>
              <th className="px-4 py-3">Subject</th>
              {isStaff(persona) && <th className="px-4 py-3">Customer</th>}
              <th className="px-4 py-3">Category</th>
              <th className="px-4 py-3">Priority</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Created by</th>
              <th className="px-4 py-3">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {rows.map((t) => (
              <tr key={t.id} className="hover:bg-white/[0.02]">
                <td className="px-4 py-3 text-slate-400">{t.id}</td>
                <td className="min-w-56 px-4 py-3 text-slate-100">{t.subject}</td>
                {isStaff(persona) && <td className="px-4 py-3 text-slate-400">{t.customer_id}</td>}
                <td className="px-4 py-3 capitalize text-slate-300">{t.category}</td>
                <td className="px-4 py-3"><Badge value={t.priority} /></td>
                <td className="px-4 py-3"><Badge value={t.status} /></td>
                <td className="px-4 py-3 text-slate-400">{t.created_by}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-400">{formatDate(t.updated_at)}</td>
              </tr>
            ))}
            {!rows.length && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-slate-500">No tickets.</td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
