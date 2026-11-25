"""Markdown-aware chunking: split on `##` headings, then pack paragraphs up to a size budget."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    heading: str | None
    content: str


def parse_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def chunk_markdown(markdown: str, max_chars: int = 900, overlap_chars: int = 120) -> list[Chunk]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in markdown.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif line.startswith("# "):
            continue
        else:
            sections[-1][1].append(line)

    chunks: list[Chunk] = []
    for heading, lines in sections:
        paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
        buf = ""
        for para in paragraphs:
            if buf and len(buf) + len(para) + 2 > max_chars:
                chunks.append(Chunk(heading, buf))
                buf = buf[-overlap_chars:] + "\n\n" + para if overlap_chars else para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
            while len(buf) > max_chars:  # a single very long paragraph
                chunks.append(Chunk(heading, buf[:max_chars]))
                buf = buf[max_chars - overlap_chars :]
        if buf:
            chunks.append(Chunk(heading, buf))
    return chunks
