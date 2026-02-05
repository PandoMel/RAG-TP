from datetime import datetime
from pathlib import Path

from celery import Celery
from redis import Redis
from sqlalchemy import text

from .clients.services import MineruClient, OCRClient
from .config import settings
from .db import SessionLocal
from .pipeline.parsers import parse_docx, parse_pdf_builtin, parse_txt, parse_xlsx

celery_app = Celery("worker", broker=settings.redis_url, backend=settings.redis_url)
redis_client = Redis.from_url(settings.redis_url)


def _update_job(db, job_id: int, status: str, step: str, progress: int, message: str | None = None):
    db.execute(
        text("UPDATE jobs SET status=:status, current_step=:step, progress=:progress, message=:message WHERE id=:id"),
        {"id": job_id, "status": status, "step": step, "progress": progress, "message": message},
    )
    db.execute(
        text("INSERT INTO job_steps (job_id, step_name, status, progress, message) VALUES (:job_id, :step_name, :status, :progress, :message)"),
        {"job_id": job_id, "step_name": step, "status": status, "progress": progress, "message": message},
    )


def _chunk_text(content: str, size: int, overlap: int):
    chunks = []
    start = 0
    while start < len(content):
        end = min(len(content), start + size)
        chunks.append(content[start:end])
        start += max(1, size - overlap)
    return chunks


def _embed_text(text_value: str):
    vec = [0.0] * settings.embedding_dim
    for idx, ch in enumerate(text_value[: settings.embedding_max_chars]):
        vec[idx % settings.embedding_dim] += (ord(ch) % 23) / 100.0
    return "[" + ",".join(f"{v:.4f}" for v in vec) + "]"


@celery_app.task(name="worker.ingest_uploaded_document")
def ingest_uploaded_document(document_id: int, job_id: int):
    db = SessionLocal()
    try:
        _update_job(db, job_id, "running", "read_document", 10)
        doc = db.execute(text("SELECT id, storage_path, title FROM documents WHERE id=:id"), {"id": document_id}).mappings().first()
        if not doc:
            _update_job(db, job_id, "failed", "read_document", 100, "Документ не найден")
            db.commit()
            return

        path = Path(doc["storage_path"])
        ext = path.suffix.lower()
        content = ""
        parser_used = "builtin"
        quality_score = 0.0
        warnings = []
        ocr_pages_processed = 0

        if ext == ".pdf":
            content, quality_score = parse_pdf_builtin(path)
            if quality_score < settings.quality_threshold_builtin:
                parser_used = "mineru"
                _update_job(db, job_id, "running", "mineru", 35)
                text_v, score_v = MineruClient(settings.mineru_url, settings.parser_timeout_seconds).parse_pdf(str(path))
                if score_v > quality_score:
                    content, quality_score = text_v, score_v
                if quality_score < settings.quality_threshold_mineru:
                    parser_used = "paddleocr"
                    _update_job(db, job_id, "running", "paddleocr", 55)
                    ocr_text, ocr_score, pages = OCRClient(settings.ocr_url, settings.ocr_timeout_seconds).parse_pdf(str(path))
                    if ocr_score > quality_score:
                        content, quality_score = ocr_text, ocr_score
                    ocr_pages_processed = pages
                    if quality_score < settings.quality_threshold_ocr:
                        warnings.append("Низкое качество OCR")
        elif ext == ".txt":
            content, quality_score = parse_txt(path)
        elif ext == ".docx":
            content, quality_score = parse_docx(path)
        elif ext == ".xlsx":
            content, quality_score = parse_xlsx(path)
        else:
            _update_job(db, job_id, "failed", "validate_extension", 100, "Расширение не поддерживается")
            db.commit()
            return

        _update_job(db, job_id, "running", "chunk_embed", 75)
        chunks = _chunk_text(content, settings.chunk_size_chars, settings.chunk_overlap_chars)
        for idx, chunk in enumerate(chunks):
            db.execute(
                text(
                    "INSERT INTO chunks (document_id, chunk_index, content, embedding, meta) VALUES (:document_id, :chunk_index, :content, CAST(:embedding AS vector), :meta::jsonb)"
                ),
                {
                    "document_id": document_id,
                    "chunk_index": idx,
                    "content": chunk,
                    "embedding": _embed_text(chunk),
                    "meta": '{"page_or_sheet": null}',
                },
            )

        db.execute(
            text(
                "UPDATE documents SET status='ready', meta=:meta::jsonb WHERE id=:id"
            ),
            {
                "id": document_id,
                "meta": str(
                    {
                        "parser_used": parser_used,
                        "quality_score": quality_score,
                        "warnings": warnings,
                        "ocr_pages_processed": ocr_pages_processed,
                    }
                ).replace("'", '"'),
            },
        )
        _update_job(db, job_id, "completed", "done", 100)
        db.commit()
    except Exception as exc:
        _update_job(db, job_id, "failed", "error", 100, f"Ошибка пайплайна: {exc}")
        db.commit()
        raise
    finally:
        db.close()


@celery_app.task(name="worker.scan_source_incremental")
def scan_source_incremental(source_id: int):
    return f"incremental scan source_id={source_id}"


@celery_app.task(name="worker.scan_source_full_audit")
def scan_source_full_audit(source_id: int):
    return f"full audit source_id={source_id}"


@celery_app.task(name="worker.cleanup_expired_temp")
def cleanup_expired_temp():
    db = SessionLocal()
    try:
        db.execute(text("UPDATE documents SET deleted_at=:deleted_at WHERE scope='temp' AND expires_at < NOW() AND deleted_at IS NULL"), {"deleted_at": datetime.utcnow()})
        db.commit()
    finally:
        db.close()
