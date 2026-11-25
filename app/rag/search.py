"""Hybrid retrieval: pgvector cosine search + Postgres full-text search, fused with RRF."""

from __future__ import annotations

from dataclasses import dataclass

from app.db import get_pool
from app.rag.embeddings import get_embedder, to_pgvector

# Reciprocal Rank Fusion constant (standard value from Cormack et al.)
RRF_K = 60

HYBRID_SQL = """
WITH vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(emb)s::vector) AS rnk
    FROM kb_chunks
    ORDER BY embedding <=> %(emb)s::vector
    LIMIT %(pool)s
),
kw AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC) AS rnk
    FROM kb_chunks c, websearch_to_tsquery('english', %(q)s) q
    WHERE c.tsv @@ q
    ORDER BY ts_rank_cd(c.tsv, q) DESC
    LIMIT %(pool)s
)
SELECT c.id, d.slug, d.title, c.heading, c.content,
       COALESCE(1.0 / (%(k)s + vec.rnk), 0) + COALESCE(1.0 / (%(k)s + kw.rnk), 0) AS score,
       vec.rnk AS vector_rank, kw.rnk AS keyword_rank
FROM kb_chunks c
JOIN kb_documents d ON d.id = c.document_id
LEFT JOIN vec ON vec.id = c.id
LEFT JOIN kw  ON kw.id  = c.id
WHERE vec.id IS NOT NULL OR kw.id IS NOT NULL
ORDER BY score DESC
LIMIT %(top_k)s
"""


@dataclass
class SearchHit:
    chunk_id: int
    slug: str
    title: str
    heading: str | None
    content: str
    score: float
    vector_rank: int | None
    keyword_rank: int | None

    @property
    def citation(self) -> str:
        return f"{self.slug}#{self.heading or 'intro'}"


async def hybrid_search(query: str, top_k: int = 4, candidate_pool: int = 20) -> list[SearchHit]:
    emb = await get_embedder().embed_query(query)
    async with get_pool().connection() as conn:
        rows = await (
            await conn.execute(
                HYBRID_SQL,
                {
                    "emb": to_pgvector(emb),
                    "q": query,
                    "pool": candidate_pool,
                    "k": RRF_K,
                    "top_k": top_k,
                },
            )
        ).fetchall()
    return [
        SearchHit(
            chunk_id=r["id"],
            slug=r["slug"],
            title=r["title"],
            heading=r["heading"],
            content=r["content"],
            score=float(r["score"]),
            vector_rank=r["vector_rank"],
            keyword_rank=r["keyword_rank"],
        )
        for r in rows
    ]
