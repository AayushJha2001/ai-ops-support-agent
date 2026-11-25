from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage


def text_of(message: BaseMessage) -> str:
    """Plain text of a message whose content may be a string or a list of content blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type", "text") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def trim_history(messages: list[BaseMessage], max_messages: int = 40) -> list[BaseMessage]:
    """Keep the most recent turns, cutting only at a HumanMessage boundary so tool-call /
    tool-result pairs are never split (providers reject orphaned tool results)."""
    if len(messages) <= max_messages:
        return messages
    for i, m in enumerate(messages):
        if isinstance(m, HumanMessage) and len(messages) - i <= max_messages:
            return messages[i:]
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    return messages[last_human:]
