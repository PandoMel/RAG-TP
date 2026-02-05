from pathlib import Path
import shutil

from celery import Celery
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..schemas import ChatRequest, ChatResponse, JobOut, UploadResponse
from ..services.chat import ChatService
from ..services.security import ensure_safe_path

router = APIRouter(prefix="/v1")
celery_app = Celery("api", broker=settings.redis_url, backend=settings.redis_url)


@router.get("/config/public")
def public_config():
    return {
        "file_whitelist": settings.file_whitelist.split(","),
        "upload_max_mb": settings.upload_max_mb,
        "temp_upload_ttl_hours": settings.temp_upload_ttl_hours,
        "bm25_top_k": settings.bm25_top_k,
        "vector_top_k": settings.vector_top_k,
        "final_top_n": settings.final_top_n,
    }


@router.post("/files/upload", response_model=UploadResponse)
def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    suffix = file.filename.split(".")[-1].lower()
    if suffix not in settings.file_whitelist.split(","):
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / file.filename
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    doc = db.execute(
        text(
            """
            INSERT INTO documents (scope, title, storage_path, relative_path, status, expires_at)
            VALUES ('temp', :title, :storage_path, :relative_path, 'queued', NOW() + (:ttl || ' hours')::interval)
            RETURNING id
            """
        ),
        {"title": file.filename, "storage_path": str(target), "relative_path": file.filename, "ttl": settings.temp_upload_ttl_hours},
    ).scalar_one()
    job_id = db.execute(
        text("INSERT INTO jobs (job_type, document_id, status, progress, current_step) VALUES ('ingest_upload', :document_id, 'queued', 0, 'queued') RETURNING id"),
        {"document_id": doc},
    ).scalar_one()
    db.commit()

    celery_app.send_task("worker.ingest_uploaded_document", args=[doc, job_id])
    return UploadResponse(document_id=doc, job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}).mappings().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job не найден")
    steps = db.execute(text("SELECT step_name, status, progress, message FROM job_steps WHERE job_id=:job_id ORDER BY id"), {"job_id": job_id}).mappings().all()
    return {**job, "steps": steps}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    return db.execute(text("SELECT id, status, current_step, progress, queue_position, file_name, file_size_mb FROM jobs WHERE status IN ('queued','running') ORDER BY queue_position NULLS LAST, created_at"))\
        .mappings().all()


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    return ChatService(db).ask(payload, settings)


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)):
    return db.execute(text("SELECT * FROM sources ORDER BY id")).mappings().all()


@router.get("/documents/{document_id}/view")
def view_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.execute(text("SELECT storage_path, relative_path FROM documents WHERE id=:id"), {"id": document_id}).mappings().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    safe_path = ensure_safe_path(Path(settings.upload_dir), doc["relative_path"])
    return FileResponse(safe_path, media_type="application/pdf")


@router.get("/documents/{document_id}/download")
def download_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.execute(text("SELECT storage_path, relative_path FROM documents WHERE id=:id"), {"id": document_id}).mappings().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    safe_path = ensure_safe_path(Path(settings.upload_dir), doc["relative_path"])
    return FileResponse(safe_path, filename=Path(doc["relative_path"]).name)


@router.get("/admin", response_class=HTMLResponse)
def admin_page():
    if not settings.admin_ui_enabled:
        raise HTTPException(status_code=404, detail="Admin disabled")
    return "<h1>Admin</h1><p>Управление sources, jobs и аудитом.</p>"
