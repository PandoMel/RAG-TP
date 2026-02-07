from contextlib import contextmanager
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
import time

from celery import Celery
from redis import Redis
from sqlalchemy import text

from .clients.services import MineruClient, OCRClient
from .config import settings
from .db import SessionLocal
from .embeddings import get_embedder
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


def _format_vector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _embed_texts(texts: list[str]) -> list[str]:
    embedder = get_embedder()
    batch = embedder.embed_texts(texts)
    return [_format_vector(vec) for vec in batch.embeddings]


@contextmanager
def _gpu_lock():
    """Глобальная блокировка GPU для тяжелых задач OCR/MinerU."""
    start = time.time()
    while True:
        acquired = redis_client.set(settings.gpu_lock_key, "1", nx=True, ex=settings.gpu_lock_ttl_seconds)
        if acquired:
            break
        if time.time() - start > settings.gpu_lock_ttl_seconds:
            raise RuntimeError("Таймаут ожидания GPU lock")
        time.sleep(1)
    try:
        yield
    finally:
        redis_client.delete(settings.gpu_lock_key)


def _create_job(db, job_type: str, source_id: int | None = None, document_id: int | None = None) -> int:
    return db.execute(
        text(
            "INSERT INTO jobs (job_type, source_id, document_id, status, progress, current_step) "
            "VALUES (:job_type, :source_id, :document_id, 'running', 0, 'queued') RETURNING id"
        ),
        {"job_type": job_type, "source_id": source_id, "document_id": document_id},
    ).scalar_one()


def _resolve_source_base(base_path: str) -> Path:
    root = Path(settings.nas_mount_path).resolve()
    candidate = Path(base_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not str(candidate).startswith(str(root)):
        raise ValueError("Base path выходит за пределы NAS")
    return candidate


def _matches_globs(relative_path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
    if include_globs and not any(fnmatch(relative_path, pat) for pat in include_globs):
        return False
    if exclude_globs and any(fnmatch(relative_path, pat) for pat in exclude_globs):
        return False
    return True


def _ingest_file(db, document_id: int, path: Path, job_id: int | None):
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
            if job_id:
                _update_job(db, job_id, "running", "mineru", 35)
            with _gpu_lock():
                text_v, score_v = MineruClient(settings.mineru_url, settings.parser_timeout_seconds).parse_pdf(str(path))
            if score_v > quality_score:
                content, quality_score = text_v, score_v
            if quality_score < settings.quality_threshold_mineru:
                parser_used = "paddleocr"
                if job_id:
                    _update_job(db, job_id, "running", "paddleocr", 55)
                with _gpu_lock():
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
        raise ValueError("Расширение не поддерживается")

    chunks = _chunk_text(content, settings.chunk_size_chars, settings.chunk_overlap_chars)
    for start in range(0, len(chunks), settings.embedding_batch_size):
        batch_chunks = chunks[start : start + settings.embedding_batch_size]
        embeddings = _embed_texts(batch_chunks)
        for offset, (chunk, embedding) in enumerate(zip(batch_chunks, embeddings)):
            db.execute(
                text(
                    "INSERT INTO chunks (document_id, chunk_index, content, embedding, meta) "
                    "VALUES (:document_id, :chunk_index, :content, CAST(:embedding AS vector), :meta::jsonb)"
                ),
                {
                    "document_id": document_id,
                    "chunk_index": start + offset,
                    "content": chunk,
                    "embedding": embedding,
                    "meta": '{"page_or_sheet": null}',
                },
            )

    existing_meta = db.execute(text("SELECT meta FROM documents WHERE id=:id"), {"id": document_id}).scalar()
    meta = existing_meta or {}
    meta.update(
        {
            "parser_used": parser_used,
            "quality_score": quality_score,
            "warnings": warnings,
            "ocr_pages_processed": ocr_pages_processed,
        }
    )
    db.execute(
        text("UPDATE documents SET status='ready', meta=:meta::jsonb WHERE id=:id"),
        {"id": document_id, "meta": str(meta).replace(\"'\", '\"')},
    )


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
        if path.suffix.lower() not in {".pdf", ".txt", ".docx", ".xlsx"}:
            _update_job(db, job_id, "failed", "validate_extension", 100, "Расширение не поддерживается")
            db.commit()
            return

        _update_job(db, job_id, "running", "chunk_embed", 75)
        _ingest_file(db, document_id, path, job_id)
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
    db = SessionLocal()
    job_id = None
    try:
        job_id = _create_job(db, "scan_incremental", source_id=source_id)
        _update_job(db, job_id, "running", "scan_start", 5)
        source = db.execute(
            text("SELECT id, base_path, include_globs, exclude_globs FROM sources WHERE id=:id AND enabled=TRUE"),
            {"id": source_id},
        ).mappings().first()
        if not source:
            _update_job(db, job_id, "failed", "scan_start", 100, "Источник не найден")
            db.commit()
            return

        base_path = _resolve_source_base(source["base_path"])
        include_globs = source["include_globs"] or []
        exclude_globs = source["exclude_globs"] or []
        allowed = {ext.strip().lower() for ext in settings.allowed_extensions.split(",") if ext.strip()}

        scanned_files = 0
        scanned_mb = 0.0
        start_time = time.time()

        for file_path in base_path.rglob("*"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(base_path).as_posix()
            if not _matches_globs(rel_path, include_globs, exclude_globs):
                continue
            if file_path.suffix.lower().lstrip(".") not in allowed:
                continue

            stat = file_path.stat()
            file_mb = stat.st_size / (1024 * 1024)
            if scanned_files >= settings.scan_max_files or scanned_mb + file_mb > settings.scan_max_mb:
                break
            if time.time() - start_time > settings.scan_timeout_seconds:
                break

            scanned_files += 1
            scanned_mb += file_mb
            progress = int(min(90, (scanned_files / max(1, settings.scan_max_files)) * 90))
            _update_job(db, job_id, "running", "index_file", progress, f"Индексирование {rel_path}")

            doc = db.execute(
                text("SELECT id, meta FROM documents WHERE source_id=:source_id AND relative_path=:relative_path"),
                {"source_id": source_id, "relative_path": rel_path},
            ).mappings().first()
            meta = doc["meta"] if doc else {}
            mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
            if doc and meta.get("mtime") == mtime and meta.get("size_bytes") == stat.st_size:
                continue

            if doc:
                document_id = doc["id"]
                db.execute(text("DELETE FROM chunks WHERE document_id=:document_id"), {"document_id": document_id})
                db.execute(
                    text("UPDATE documents SET status='queued', storage_path=:storage_path, meta=:meta::jsonb WHERE id=:id"),
                    {
                        "id": document_id,
                        "storage_path": str(file_path),
                        "meta": str({"mtime": mtime, "size_bytes": stat.st_size}).replace("'", '"'),
                    },
                )
            else:
                document_id = db.execute(
                    text(
                        "INSERT INTO documents (source_id, scope, title, relative_path, storage_path, status, meta) "
                        "VALUES (:source_id, 'nas', :title, :relative_path, :storage_path, 'queued', :meta::jsonb) "
                        "RETURNING id"
                    ),
                    {
                        "source_id": source_id,
                        "title": file_path.name,
                        "relative_path": rel_path,
                        "storage_path": str(file_path),
                        "meta": str({"mtime": mtime, "size_bytes": stat.st_size}).replace("'", '"'),
                    },
                ).scalar_one()

            _ingest_file(db, document_id, file_path, job_id)

        _update_job(db, job_id, "completed", "done", 100)
        db.commit()
    except Exception as exc:
        if job_id is not None:
            _update_job(db, job_id, "failed", "error", 100, f"Ошибка сканирования: {exc}")
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(name="worker.scan_source_full_audit")
def scan_source_full_audit(source_id: int):
    db = SessionLocal()
    job_id = None
    try:
        job_id = _create_job(db, "scan_full_audit", source_id=source_id)
        _update_job(db, job_id, "running", "audit_start", 5)
        source = db.execute(
            text("SELECT id, base_path, include_globs, exclude_globs FROM sources WHERE id=:id AND enabled=TRUE"),
            {"id": source_id},
        ).mappings().first()
        if not source:
            _update_job(db, job_id, "failed", "audit_start", 100, "Источник не найден")
            db.commit()
            return

        base_path = _resolve_source_base(source["base_path"])
        include_globs = source["include_globs"] or []
        exclude_globs = source["exclude_globs"] or []
        allowed = {ext.strip().lower() for ext in settings.allowed_extensions.split(",") if ext.strip()}

        scanned_files = 0
        scanned_mb = 0.0
        start_time = time.time()

        for file_path in base_path.rglob("*"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(base_path).as_posix()
            if not _matches_globs(rel_path, include_globs, exclude_globs):
                continue
            if file_path.suffix.lower().lstrip(".") not in allowed:
                continue

            stat = file_path.stat()
            file_mb = stat.st_size / (1024 * 1024)
            if scanned_files >= settings.scan_max_files or scanned_mb + file_mb > settings.scan_max_mb:
                break
            if time.time() - start_time > settings.scan_timeout_seconds:
                break

            scanned_files += 1
            scanned_mb += file_mb
            progress = int(min(90, (scanned_files / max(1, settings.scan_max_files)) * 90))
            _update_job(db, job_id, "running", "index_file", progress, f"Индексирование {rel_path}")

            doc = db.execute(
                text("SELECT id FROM documents WHERE source_id=:source_id AND relative_path=:relative_path"),
                {"source_id": source_id, "relative_path": rel_path},
            ).mappings().first()
            if doc:
                document_id = doc["id"]
                db.execute(text("DELETE FROM chunks WHERE document_id=:document_id"), {"document_id": document_id})
                db.execute(
                    text("UPDATE documents SET status='queued', storage_path=:storage_path WHERE id=:id"),
                    {"id": document_id, "storage_path": str(file_path)},
                )
            else:
                document_id = db.execute(
                    text(
                        "INSERT INTO documents (source_id, scope, title, relative_path, storage_path, status) "
                        "VALUES (:source_id, 'nas', :title, :relative_path, :storage_path, 'queued') RETURNING id"
                    ),
                    {
                        "source_id": source_id,
                        "title": file_path.name,
                        "relative_path": rel_path,
                        "storage_path": str(file_path),
                    },
                ).scalar_one()
            _ingest_file(db, document_id, file_path, job_id)

        _update_job(db, job_id, "completed", "done", 100)
        db.commit()
    except Exception as exc:
        if job_id is not None:
            _update_job(db, job_id, "failed", "error", 100, f"Ошибка аудита: {exc}")
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(name="worker.cleanup_expired_temp")
def cleanup_expired_temp():
    db = SessionLocal()
    try:
        db.execute(text("UPDATE documents SET deleted_at=:deleted_at WHERE scope='temp' AND expires_at < NOW() AND deleted_at IS NULL"), {"deleted_at": datetime.utcnow()})
        db.commit()
    finally:
        db.close()
