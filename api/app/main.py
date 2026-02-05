from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .config import settings
from .routers.v1 import router as v1_router

app = FastAPI(title=settings.app_name)
app.include_router(v1_router)


@app.get("/")
def root():
    return FileResponse(Path(__file__).parent / "ui" / "index.html")
