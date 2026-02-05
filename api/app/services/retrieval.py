from collections import defaultdict
from sqlalchemy import text


class RetrievalService:
    def __init__(self, db):
        self.db = db

    def _embed_query(self, query: str) -> str:
        # Простой детерминированный embedding stub, заменяется на BGE-M3 в worker-сервисе.
        vec = [0.0] * 8
        for idx, ch in enumerate(query[:64]):
            vec[idx % 8] += (ord(ch) % 31) / 100.0
        return "[" + ",".join(f"{x:.4f}" for x in vec) + "]"

    def hybrid_search(self, query: str, mode: str, source_ids: list[int], temp_document_id: int | None, bm25_top_k: int, vector_top_k: int, rrf_k: int, final_top_n: int):
        filters = ["d.deleted_at IS NULL"]
        params = {"query": query, "bm25_top_k": bm25_top_k, "vector_top_k": vector_top_k, "rrf_k": rrf_k}

        if mode == "temp" and temp_document_id is not None:
            filters.append("d.id = :temp_document_id")
            params["temp_document_id"] = temp_document_id
        elif mode == "nas" and source_ids:
            filters.append("d.source_id = ANY(:source_ids)")
            params["source_ids"] = source_ids

        where_clause = " AND ".join(filters)
        bm25_sql = text(f"""
            SELECT c.id, c.document_id, c.content,
                   ROW_NUMBER() OVER (ORDER BY paradedb.score(c.id) DESC) AS rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {where_clause} AND c.content @@@ :query
            ORDER BY paradedb.score(c.id) DESC
            LIMIT :bm25_top_k
        """)
        vector_sql = text(f"""
            SELECT c.id, c.document_id, c.content,
                   ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:embedding AS vector)) AS rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {where_clause}
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :vector_top_k
        """)

        params["embedding"] = self._embed_query(query)
        bm25_rows = self.db.execute(bm25_sql, params).mappings().all()
        vec_rows = self.db.execute(vector_sql, params).mappings().all()

        scored: dict[int, float] = defaultdict(float)
        rows_by_id = {}
        for row in bm25_rows:
            rows_by_id[row["id"]] = row
            scored[row["id"]] += 1.0 / (rrf_k + row["rank"])
        for row in vec_rows:
            rows_by_id[row["id"]] = row
            scored[row["id"]] += 1.0 / (rrf_k + row["rank"])

        ranked = sorted(scored.items(), key=lambda x: x[1], reverse=True)[:final_top_n]
        chunk_ids = [chunk_id for chunk_id, _ in ranked]
        return [rows_by_id[cid] for cid in chunk_ids]
