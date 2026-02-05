from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="reranker")


class RerankRequest(BaseModel):
    query: str
    passages: list[str]
    top_n: int = 5


@app.post('/v1/rerank')
def rerank(payload: RerankRequest):
    scored = []
    for idx, passage in enumerate(payload.passages):
        overlap = len(set(payload.query.lower().split()) & set(passage.lower().split()))
        scored.append((idx, float(overlap)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return {"items": [{"index": idx, "score": score} for idx, score in scored[: payload.top_n]]}
