from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "RAG-TP API"
    debug_logs: bool = False
    log_level_default: str = "WARNING"

    postgres_dsn: str = "postgresql+psycopg2://rag:rag@postgres:5432/rag"
    redis_url: str = "redis://redis:6379/0"

    upload_dir: str = "/data/uploads"
    temp_upload_ttl_hours: int = 24
    upload_max_mb: int = 50
    file_whitelist: str = "pdf,docx,xlsx,txt"

    # Hybrid retrieval
    bm25_top_k: int = 20
    vector_top_k: int = 20
    rrf_k: int = 60
    final_top_n: int = 12
    rerank_top_n: int = 8
    context_top_m: int = 5
    embedding_dim: int = 8

    # Timeouts
    chat_timeout_seconds: int = 60
    rerank_timeout_seconds: int = 30

    # External services
    reranker_url: str = "http://reranker:8090/v1/rerank"
    llm_base_url: str = "http://llm:9000/v1/chat"

    # NAS
    nas_mount_path: str = "/mnt/nas"
    nas_smb_server: str = "//nas/share"
    nas_username: str = "nas_user"
    nas_password: str = "nas_password"

    admin_ui_enabled: bool = True


settings = Settings()
