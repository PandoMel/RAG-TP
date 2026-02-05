import httpx


class MineruClient:
    def __init__(self, url: str, timeout: int):
        self.url = url
        self.timeout = timeout

    def parse_pdf(self, file_path: str) -> tuple[str, float]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.url, json={"file_path": file_path})
            response.raise_for_status()
            data = response.json()
        return data.get("text", ""), data.get("quality_score", 0.0)


class OCRClient:
    def __init__(self, url: str, timeout: int):
        self.url = url
        self.timeout = timeout

    def parse_pdf(self, file_path: str) -> tuple[str, float, int]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.url, json={"file_path": file_path})
            response.raise_for_status()
            data = response.json()
        return data.get("text", ""), data.get("quality_score", 0.0), data.get("pages_processed", 0)
