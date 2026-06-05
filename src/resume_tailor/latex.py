import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def find_latex_compiler() -> Optional[str]:
    for candidate in ("xelatex", "pdflatex"):
        if shutil.which(candidate):
            return candidate
    return None


def compile_tex(tex_path: Path, compiler: str) -> Optional[Path]:
    """Compile a .tex file to PDF in its parent directory. Returns the PDF path or None on failure."""
    out_dir = tex_path.parent
    cmd = [
        compiler,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-output-directory",
        str(out_dir),
        str(tex_path),
    ]
    # Run twice for hyperref/labels; second pass is cheap.
    for _ in range(2):
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(out_dir)
        )
        if result.returncode != 0:
            log_path = tex_path.with_suffix(".log")
            print(
                f"  {compiler} failed (rc={result.returncode}). "
                f"See {log_path} for details.",
                file=sys.stderr,
            )
            return None

    pdf_path = tex_path.with_suffix(".pdf")
    return pdf_path if pdf_path.exists() else None
