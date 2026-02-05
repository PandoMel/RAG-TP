from datetime import datetime
from pydantic import BaseModel


class UploadResponse(BaseModel):
    document_id: int
    job_id: int


class JobStepOut(BaseModel):
    step_name: str
    status: str
    progress: int
    message: str | None


class JobOut(BaseModel):
    id: int
    status: str
    current_step: str | None
    progress: int
    message: str | None
    created_at: datetime
    steps: list[JobStepOut]


class ChatRequest(BaseModel):
    mode: str = "temp"
    question: str
    source_ids: list[int] = []
    subpath: str | None = None
    temp_document_id: int | None = None


class Citation(BaseModel):
    doc_id: int
    title: str
    relative_path: str
    page_or_sheet: str | None
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
