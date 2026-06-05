from pathlib import Path

from pypdf import PdfReader


def load_resume(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()
    if suffix in {".md", ".markdown", ".txt"}:
        return path.read_text(encoding="utf-8").strip()
    raise ValueError(
        f"Unsupported resume format: {suffix!r}. Use .pdf, .md, or .txt."
    )
