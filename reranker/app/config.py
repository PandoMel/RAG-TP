from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    models_dir: str = "/models"
    reranker_model_path: str = "/models/bge-reranker-v2-gemma/model.onnx"
    reranker_onnx_providers: list[str] = ["CPUExecutionProvider"]
    reranker_max_tokens: int = 512


settings = Settings()
