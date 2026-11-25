from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.messages import trim_history
from app.agent.policy import approval_reason
from app.rag.chunking import chunk_markdown, parse_title


def test_refunds_always_need_approval():
    assert approval_reason(
        "issue_refund", {"amount_usd": 5, "invoice_number": "INV-1-1"}, "risk_based"
    )


def test_low_risk_writes_run_immediately_in_risk_mode():
    assert approval_reason("create_ticket", {"priority": "normal"}, "risk_based") is None
    assert approval_reason("update_ticket", {"ticket_id": 1, "note": "hi"}, "risk_based") is None


def test_risky_ticket_changes_need_approval():
    assert approval_reason("update_ticket", {"ticket_id": 1, "status": "closed"}, "risk_based")
    assert approval_reason("create_ticket", {"priority": "urgent"}, "risk_based")


def test_all_writes_mode_and_escalation_never_gated():
    assert approval_reason("create_ticket", {"priority": "low"}, "all_writes")
    assert approval_reason("escalate_to_human", {"severity": "high"}, "all_writes") is None
    assert approval_reason("search_knowledge_base", {}, "all_writes") is None


def test_chunking_splits_on_headings():
    md = "# Title\n\nintro text\n\n## A\n\nalpha para\n\n## B\n\n" + ("beta " * 400)
    chunks = chunk_markdown(md, max_chars=500, overlap_chars=50)
    assert parse_title(md, "x") == "Title"
    assert chunks[0].heading is None and chunks[1].heading == "A"
    assert all(len(c.content) <= 500 for c in chunks)
    assert sum(c.heading == "B" for c in chunks) >= 3


def test_trim_history_never_splits_tool_pairs():
    msgs = []
    for i in range(30):
        msgs += [
            HumanMessage(f"q{i}"),
            AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": f"c{i}"}]),
            ToolMessage("r", tool_call_id=f"c{i}"),
            AIMessage(f"a{i}"),
        ]
    trimmed = trim_history(msgs, max_messages=10)
    assert isinstance(trimmed[0], HumanMessage) and len(trimmed) <= 10
