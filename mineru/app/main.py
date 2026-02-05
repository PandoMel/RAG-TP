from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mineru")


class ParseRequest(BaseModel):
    file_path: str


@app.post('/v1/parse')
def parse(payload: ParseRequest):
    # Заглушка MinerU-сервиса для smoke и локального запуска.
    return {"text": f"MinerU text for {payload.file_path}", "quality_score": 0.72}
