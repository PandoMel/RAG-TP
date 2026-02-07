from fastapi import FastAPI
import numpy as np
import onnxruntime as ort
from pydantic import BaseModel
from transformers import AutoTokenizer

from .config import settings

app = FastAPI(title="reranker")
_SESSION = ort.InferenceSession(settings.reranker_model_path, providers=settings.reranker_onnx_providers)
_TOKENIZER = AutoTokenizer.from_pretrained(settings.models_dir + "/bge-reranker-v2-gemma", use_fast=True)
_INPUT_NAMES = {inp.name for inp in _SESSION.get_inputs()}


class RerankRequest(BaseModel):
    query: str
    passages: list[str]
    top_n: int = 5


@app.post('/v1/rerank')
def rerank(payload: RerankRequest):
    if not payload.passages:
        return {"items": []}
    tokens = _TOKENIZER(
        [payload.query] * len(payload.passages),
        payload.passages,
        padding=True,
        truncation=True,
        max_length=settings.reranker_max_tokens,
        return_tensors="np",
    )
    feeds = {name: tokens[name] for name in _INPUT_NAMES if name in tokens}
    outputs = _SESSION.run(None, feeds)
    scores = _select_scores(outputs)
    scored = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    return {"items": [{"index": idx, "score": float(score)} for idx, score in scored[: payload.top_n]]}


def _select_scores(outputs: list[np.ndarray]) -> list[float]:
    if not outputs:
        return []
    output = outputs[0]
    if output.ndim == 2 and output.shape[1] == 1:
        return output[:, 0].astype(np.float32).tolist()
    if output.ndim == 1:
        return output.astype(np.float32).tolist()
    if output.ndim == 2:
        return output[:, 0].astype(np.float32).tolist()
    raise RuntimeError(f"Неожиданная размерность выходов ONNX: {output.ndim}")
