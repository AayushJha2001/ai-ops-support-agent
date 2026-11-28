import type { Metadata } from "next";
import { Suspense } from "react";
import Shell from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Ops Console",
  description: "Operator console for the AI Operations & Support Agent (LangGraph, FastAPI, PostgreSQL).",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      {/* Browser extensions (e.g. Grammarly) add attributes to <body>; don't treat that as a mismatch. */}
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased" suppressHydrationWarning>
        <Suspense fallback={<div className="p-8 text-sm text-slate-400">Loading console…</div>}>
          <Shell>{children}</Shell>
        </Suspense>
      </body>
    </html>
  );
}
