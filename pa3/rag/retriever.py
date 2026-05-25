"""PA3 retrieval adapter over the PA2 pgvector corpus and reranker."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
PA2_SRC_DIR = REPO_ROOT / "pa2" / "crawler" / "src"
if str(PA2_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(PA2_SRC_DIR))


class RetrievalError(RuntimeError):
    """Raised when query embedding, database search, or reranking fails."""


@dataclass(frozen=True)
class RetrievalConfig:
    host: str = os.getenv("PGHOST", "localhost")
    port: int = int(os.getenv("PGPORT", "5432"))
    database: str = os.getenv("PGDATABASE", "crawldb")
    user: str = os.getenv("PGUSER", "postgres")
    password: str = os.getenv("PGPASSWORD", "postgres")
    table: str = "page_segment_long"
    embedding_model: str = "sentence-transformers/LaBSE"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    top_k: int = 5
    rerank_candidates: int = 20
    ivfflat_probes: int = 10
    device: str | None = None
    rerank: bool = True

    def __post_init__(self) -> None:
        if self.table not in {"page_segment_long", "page_segment_short"}:
            raise ValueError("table must be page_segment_long or page_segment_short")
        if self.top_k <= 0 or self.rerank_candidates < self.top_k:
            raise ValueError("rerank_candidates must be greater than or equal to top_k")
        if self.ivfflat_probes <= 0:
            raise ValueError("ivfflat_probes must be positive")


@dataclass(frozen=True)
class RetrievalHit:
    page_id: int | None
    url: str
    segment_text: str
    cosine_distance: float
    rerank_score: float | None
    vector_rank: int
    final_rank: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Retriever:
    """Retrieve and rerank evidence without modifying the PA2 index."""

    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()
        self._embedder: Any = None

    def retrieve(self, query: str) -> list[RetrievalHit]:
        if not query or not query.strip():
            raise RetrievalError("Poizvedba ne sme biti prazna.")

        vector = self._embed_query(query.strip())
        candidates = self._vector_search(vector)
        if not candidates:
            return []
        if not self.config.rerank:
            return [
                RetrievalHit(
                    page_id=row["page_id"],
                    url=row["url"],
                    segment_text=row["segment_text"],
                    cosine_distance=row["cosine_distance"],
                    rerank_score=None,
                    vector_rank=row["vector_rank"],
                    final_rank=index,
                )
                for index, row in enumerate(candidates[: self.config.top_k], start=1)
            ]
        return self._rerank(query.strip(), candidates)

    def settings(self) -> dict[str, Any]:
        return {
            "embedding_model": self.config.embedding_model,
            "table": self.config.table,
            "metric": "cosine",
            "top_k": self.config.top_k,
            "rerank": self.config.rerank,
            "reranker_model": self.config.reranker_model if self.config.rerank else None,
            "rerank_candidates": self.config.rerank_candidates if self.config.rerank else None,
            "ivfflat_probes": self.config.ivfflat_probes,
        }

    def _embed_query(self, query: str) -> list[float]:
        try:
            from sentence_transformers import SentenceTransformer
            from device_utils import resolve_torch_device
        except ImportError as exc:
            raise RetrievalError(
                "Manjka Python odvisnost `sentence-transformers`. "
                "Namestite odvisnosti iz korenskega `requirements.txt`."
            ) from exc

        if self._embedder is None:
            device = resolve_torch_device(self.config.device)
            self._embedder = SentenceTransformer(self.config.embedding_model, device=device)
        return self._embedder.encode([query], show_progress_bar=False)[0].tolist()

    def _vector_search(self, query_vector: list[float]) -> list[dict[str, Any]]:
        try:
            import psycopg2
        except ImportError as exc:
            raise RetrievalError(
                "Manjka Python odvisnost `psycopg2-binary`; namestite projektne odvisnosti."
            ) from exc

        limit = self.config.rerank_candidates if self.config.rerank else self.config.top_k
        vector_literal = json.dumps(query_vector)
        sql = f"""
            SELECT s.page_id, COALESCE(p.url, ''), s.segment_text,
                   (s.embedding <=> %s::vector) AS cosine_distance
            FROM crawldb.{self.config.table} s
            LEFT JOIN crawldb.page p ON p.id = s.page_id
            WHERE s.embedding IS NOT NULL
            ORDER BY s.embedding <=> %s::vector
            LIMIT %s
        """
        try:
            with psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.database,
                user=self.config.user,
                password=self.config.password,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SET LOCAL ivfflat.probes = {self.config.ivfflat_probes};")
                    cursor.execute(sql, (vector_literal, vector_literal, limit))
                    rows = cursor.fetchall()
        except Exception as exc:
            raise RetrievalError(
                "Iskanje po PA2 bazi ni uspelo. Preverite, da tece PostgreSQL/pgvector "
                "in da je obnovljen PA2 dump z embeddingi. "
                f"Podrobnost: {exc}"
            ) from exc

        return [
            {
                "page_id": row[0],
                "url": row[1],
                "segment_text": row[2],
                "cosine_distance": float(row[3]),
                "vector_rank": index,
            }
            for index, row in enumerate(rows, start=1)
        ]

    def _rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RetrievalHit]:
        try:
            from rerank_crossencoder import rerank
        except ImportError as exc:
            raise RetrievalError("PA2 reranker modula ni mogoce naloziti.") from exc

        reranker_candidates = [
            {
                **candidate,
                "score": candidate["cosine_distance"],
            }
            for candidate in candidates
        ]
        try:
            ranked = rerank(
                query,
                reranker_candidates,
                text_field="segment_text",
                score_field="score",
                model_name=self.config.reranker_model,
                device=self.config.device,
                top_k=self.config.top_k,
            )
        except Exception as exc:
            raise RetrievalError(f"Reranking ni uspel: {exc}") from exc

        return [
            RetrievalHit(
                page_id=item.payload.get("page_id"),
                url=item.payload.get("url", ""),
                segment_text=item.text,
                cosine_distance=item.original_score,
                rerank_score=item.rerank_score,
                vector_rank=int(item.payload["vector_rank"]),
                final_rank=index,
            )
            for index, item in enumerate(ranked, start=1)
        ]
