from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

from .config import settings


@dataclass
class EmbeddingBatch:
    embeddings: list[list[float]]


class OnnxEmbeddingModel:
    """Легкий ONNX-энбеддер для BGE-M3."""

    def __init__(self, model_path: str):
        self._session = ort.InferenceSession(model_path, providers=settings.embedding_onnx_providers)
        self._tokenizer = AutoTokenizer.from_pretrained(settings.models_dir + "/bge-m3", use_fast=True)
        self._input_names = {inp.name for inp in self._session.get_inputs()}

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(embeddings=[])

        trimmed = [text[: settings.embedding_max_chars] for text in texts]
        tokens = self._tokenizer(
            trimmed,
            padding=True,
            truncation=True,
            max_length=settings.embedding_max_tokens,
            return_tensors="np",
        )
        feeds = {name: tokens[name] for name in self._input_names if name in tokens}
        outputs = self._session.run(None, feeds)
        embeddings = self._select_embeddings(outputs, tokens)
        normalized = [self._normalize(vec) for vec in embeddings]
        return EmbeddingBatch(embeddings=normalized)

    def _select_embeddings(self, outputs: list[np.ndarray], tokens) -> list[list[float]]:
        if not outputs:
            raise RuntimeError("ONNX модель не вернула выходы")
        output = outputs[0]
        if output.ndim == 2:
            return output.astype(np.float32).tolist()
        if output.ndim == 3:
            mask = tokens.get("attention_mask")
            if mask is None:
                raise RuntimeError("Не найдена attention_mask для mean pooling")
            mask = mask.astype(np.float32)
            summed = (output * mask[:, :, None]).sum(axis=1)
            counts = np.clip(mask.sum(axis=1, keepdims=True), 1.0, None)
            pooled = summed / counts
            return pooled.astype(np.float32).tolist()
        raise RuntimeError(f"Неожиданная размерность выходов ONNX: {output.ndim}")

    def _normalize(self, vec: list[float]) -> list[float]:
        array = np.array(vec, dtype=np.float32)
        norm = np.linalg.norm(array)
        if norm == 0:
            return array.tolist()
        return (array / norm).tolist()


_EMBEDDER: OnnxEmbeddingModel | None = None


def get_embedder() -> OnnxEmbeddingModel:
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = OnnxEmbeddingModel(settings.embedding_model_path)
    return _EMBEDDER
