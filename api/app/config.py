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

    # Models
    models_dir: str = "/models"
    embedding_model_path: str = "/models/bge-m3/sentence_transformers_fp16.onnx"
    reranker_model_path: str = "/models/bge-reranker-v2-gemma/model.onnx"
    llm_models_dir: str = "/models/llm"
    embedding_onnx_providers: list[str] = ["CPUExecutionProvider"]
    reranker_onnx_providers: list[str] = ["CPUExecutionProvider"]
    embedding_device: str = "cpu"
    reranker_device: str = "cpu"
    llm_device: str = "gpu"
    embedding_max_tokens: int = 512

    # Hybrid retrieval
    bm25_top_k: int = 20
    vector_top_k: int = 20
    rrf_k: int = 60
    final_top_n: int = 12
    rerank_top_n: int = 30
    context_top_m: int = 8
    embedding_dim: int = 1024

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
