import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def find_latex_compiler() -> Optional[str]:
    # Tectonic preferred: single 10MB binary, auto-fetches packages on demand,
    # handles multi-pass compilation internally.
    for candidate in ("tectonic", "xelatex", "pdflatex"):
        if shutil.which(candidate):
            return candidate
    return None


def _build_cmd(compiler: str, tex_path: Path, out_dir: Path) -> List[str]:
    if compiler == "tectonic":
        # Tectonic: simpler CLI, handles passes itself.
        return [
            compiler,
            "--outdir", str(out_dir),
            "--keep-logs",
            str(tex_path),
        ]
    # pdflatex / xelatex
    return [
        compiler,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-output-directory", str(out_dir),
        str(tex_path),
    ]


def compile_tex(tex_path: Path, compiler: str) -> Optional[Path]:
    """Compile a .tex file to PDF in its parent directory. Returns the PDF path or None on failure."""
    # Resolve to absolute paths: the compiler runs with cwd=out_dir, so a
    # path relative to the repo root would not resolve from inside out_dir.
    tex_path = tex_path.resolve()
    out_dir = tex_path.parent
    cmd = _build_cmd(compiler, tex_path, out_dir)

    # Tectonic handles multiple passes itself; pdflatex/xelatex need 2 runs
    # for hyperref / cross-references to settle.
    passes = 1 if compiler == "tectonic" else 2
    for _ in range(passes):
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


# ---- Optional install helper ---------------------------------------------------

def install_tectonic_macos() -> bool:
    """Try to install Tectonic via Homebrew on macOS. Returns True on success."""
    if not shutil.which("brew"):
        print("  Homebrew not found. Install it from https://brew.sh, then "
              "run: brew install tectonic", file=sys.stderr)
        return False
    print("  Running: brew install tectonic")
    try:
        result = subprocess.run(
            ["brew", "install", "tectonic"],
            text=True,
        )
        if result.returncode != 0:
            print("  brew install tectonic failed.", file=sys.stderr)
            return False
    except Exception as e:
        print(f"  brew install tectonic raised: {e}", file=sys.stderr)
        return False
    return shutil.which("tectonic") is not None


def offer_to_install_tectonic() -> Optional[str]:
    """Interactive prompt to install Tectonic. Returns the compiler name on
    success, None otherwise. Caller decides whether to retry compilation."""
    if sys.platform != "darwin":
        # Linux/Windows: just show install hint, don't auto-run.
        print("  Install Tectonic for built-in PDF output:")
        print("    https://tectonic-typesetting.github.io/en-US/install.html")
        return None

    from .interactive import _yn
    print()
    print("No LaTeX compiler found. Tectonic is the easiest option")
    print("(10MB, self-contained, fetches packages on demand).")
    if not _yn("  Install Tectonic now via Homebrew?", default=False):
        return None
    if install_tectonic_macos():
        print("  Tectonic installed.")
        return "tectonic"
    return None
