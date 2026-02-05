from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ocr")


class OCRRequest(BaseModel):
    file_path: str


@app.post('/v1/ocr')
def ocr(payload: OCRRequest):
    # Заглушка OCR-сервиса для smoke и локального запуска.
    return {"text": f"OCR text for {payload.file_path}", "quality_score": 0.88, "pages_processed": 3}
