from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    debug_logs: bool = False
    log_level_default: str = "WARNING"

    postgres_dsn: str = "postgresql+psycopg2://rag:rag@postgres:5432/rag"
    redis_url: str = "redis://redis:6379/0"

    upload_dir: str = "/data/uploads"
    temp_upload_ttl_hours: int = 24
    allowed_extensions: str = "pdf,docx,xlsx,txt"

    # Pipeline config
    parser_pipeline_order: str = "builtin,mineru,paddleocr"
    quality_threshold_builtin: float = 0.65
    quality_threshold_mineru: float = 0.75
    quality_threshold_ocr: float = 0.85
    llm_validation_enabled: bool = False

    # Chunking and embeddings
    chunk_size_chars: int = 800
    chunk_overlap_chars: int = 120
    embedding_dim: int = 8
    embedding_batch_size: int = 16
    embedding_max_chars: int = 2000

    # Limits
    parser_timeout_seconds: int = 120
    ocr_timeout_seconds: int = 180
    job_timeout_seconds: int = 900
    max_pdf_pages_for_ocr: int = 100

    # GPU lock
    gpu_lock_key: str = "gpu_lock"
    gpu_lock_ttl_seconds: int = 1200

    # Services
    mineru_url: str = "http://mineru:8070/v1/parse"
    ocr_url: str = "http://ocr:8080/v1/ocr"

    # NAS
    nas_mount_path: str = "/mnt/nas"
    nas_smb_server: str = "//nas/share"
    nas_username: str = "nas_user"
    nas_password: str = "nas_password"


settings = Settings()
