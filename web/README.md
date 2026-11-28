# AI Ops Console (Next.js)

Operator console for the AI Operations & Support Agent API.

- **Chat**: streams agent replies over SSE, showing each tool call and result live, approval pauses, and per-turn latency/tokens/cost.
- **Approvals** (staff): review risky write actions (refunds, ticket closures) and approve or reject; the paused LangGraph run resumes immediately.
- **Tickets**: customer-scoped ticket list.
- **Metrics** (staff): outcome mix, p50/p95 latency, LLM calls, tokens, cost and per-tool timings.

Sign-in is a demo persona picker that uses the API's seeded demo API keys (development only).

```bash
npm install
npm run dev        # http://localhost:3000 (expects the API on http://localhost:8000)
```

Set `NEXT_PUBLIC_API_URL` to point at a different API. The API must allow the console's origin
(`CORS_ORIGINS`, default `http://localhost:3000`).
