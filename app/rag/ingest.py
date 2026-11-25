"""Index the markdown knowledge base into kb_documents / kb_chunks."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings
from app.db import get_pool
from app.rag.chunking import chunk_markdown, parse_title
from app.rag.embeddings import get_embedder, to_pgvector

log = logging.getLogger(__name__)


async def ingest_kb(kb_dir: str | None = None) -> int:
    """(Re)index every *.md file. Returns the number of chunks written."""
    directory = Path(kb_dir or get_settings().kb_dir)
    files = sorted(directory.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"No markdown files found in {directory.resolve()}")

    embedder = get_embedder()
    total = 0
    async with get_pool().connection() as conn:
        for path in files:
            text = path.read_text(encoding="utf-8")
            slug = path.stem
            title = parse_title(text, slug)
            chunks = chunk_markdown(text)
            vectors = await embedder.embed_documents(
                [f"{title} - {c.heading or ''}\n{c.content}" for c in chunks]
            )
            async with conn.transaction():
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO kb_documents (slug, title, source_path)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (slug) DO UPDATE
                           SET title = EXCLUDED.title, source_path = EXCLUDED.source_path,
                               updated_at = now()
                        RETURNING id
                        """,
                        (slug, title, str(path.as_posix())),
                    )
                ).fetchone()
                doc_id = row["id"]
                await conn.execute("DELETE FROM kb_chunks WHERE document_id = %s", (doc_id,))
                for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True)):
                    await conn.execute(
                        """
                        INSERT INTO kb_chunks (document_id, chunk_index, heading, content, embedding)
                        VALUES (%s, %s, %s, %s, %s::vector)
                        """,
                        (doc_id, i, chunk.heading, chunk.content, to_pgvector(vec)),
                    )
            total += len(chunks)
            log.info("indexed %s (%d chunks)", slug, len(chunks))
    return total
