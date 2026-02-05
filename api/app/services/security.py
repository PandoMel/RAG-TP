from pathlib import Path


def ensure_safe_path(base_path: Path, relative_path: str) -> Path:
    """Защита от path traversal при выдаче файлов."""
    candidate = (base_path / relative_path).resolve()
    if not str(candidate).startswith(str(base_path.resolve())):
        raise ValueError("Недопустимый путь")
    return candidate
