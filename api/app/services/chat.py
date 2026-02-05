from sqlalchemy import text

from .retrieval import RetrievalService


class ChatService:
    def __init__(self, db):
        self.db = db
        self.retrieval = RetrievalService(db)

    def ask(self, payload, cfg):
        chunks = self.retrieval.hybrid_search(
            query=payload.question,
            mode=payload.mode,
            source_ids=payload.source_ids,
            temp_document_id=payload.temp_document_id,
            bm25_top_k=cfg.bm25_top_k,
            vector_top_k=cfg.vector_top_k,
            rrf_k=cfg.rrf_k,
            final_top_n=cfg.final_top_n,
        )

        # Упрощенный rerank stub: реальный rerank дергается через /v1/rerank сервис.
        selected = chunks[: cfg.rerank_top_n]

        citations = []
        snippets = []
        for row in selected[: cfg.context_top_m]:
            doc = self.db.execute(
                text("SELECT id, title, relative_path FROM documents WHERE id=:id"),
                {"id": row["document_id"]},
            ).mappings().first()
            snippet = row["content"][:280]
            snippets.append(snippet)
            citations.append(
                {
                    "doc_id": doc["id"],
                    "title": doc["title"],
                    "relative_path": doc["relative_path"],
                    "page_or_sheet": None,
                    "snippet": snippet,
                }
            )

        answer = "\n".join(["Найденные фрагменты:", *snippets]) if snippets else "Недостаточно данных для ответа."
        return {"answer": answer, "citations": citations}
