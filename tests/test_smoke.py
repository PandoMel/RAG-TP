import os

import pytest
import requests

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")


def _api_ready() -> bool:
    try:
        response = requests.get(f"{API_URL}/v1/config/public", timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(not _api_ready(), reason="API недоступен для интеграционного smoke")


def test_upload_job_status_reachable(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("пример текста для smoke", encoding="utf-8")
    with file_path.open("rb") as f:
        response = requests.post(f"{API_URL}/v1/files/upload", files={"file": ("sample.txt", f, "text/plain")}, timeout=10)
    assert response.status_code == 200
    payload = response.json()
    job_response = requests.get(f"{API_URL}/v1/jobs/{payload['job_id']}", timeout=10)
    assert job_response.status_code == 200


def test_retrieval_returns_citations():
    response = requests.post(
        f"{API_URL}/v1/chat",
        json={"mode": "temp", "question": "тест", "temp_document_id": 1},
        timeout=10,
    )
    assert response.status_code == 200
    assert "citations" in response.json()


def test_path_traversal_protection():
    response = requests.get(f"{API_URL}/v1/documents/999999/view?path=../../etc/passwd", timeout=10)
    assert response.status_code in (400, 404)
