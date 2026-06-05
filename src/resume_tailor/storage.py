"""Persistent config — stores a default resume on disk so the user doesn't
have to pass --resume on every run."""
import json
import os
import shutil
from pathlib import Path
from typing import Optional

APP_NAME = "resume-tailor"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def _meta_path() -> Path:
    return config_dir() / "config.json"


def _read_meta() -> dict:
    p = _meta_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_meta(meta: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    _meta_path().write_text(json.dumps(meta, indent=2), encoding="utf-8")


def save_resume(source: Path) -> Path:
    """Copy a resume into config dir. Returns the stored path."""
    config_dir().mkdir(parents=True, exist_ok=True)
    ext = source.suffix.lower()
    dest = config_dir() / f"resume{ext}"
    # Remove any old resume with a different extension.
    for old in config_dir().glob("resume.*"):
        if old != dest:
            old.unlink()
    shutil.copy2(source, dest)
    meta = _read_meta()
    meta["original_path"] = str(source.resolve())
    meta["stored_path"] = str(dest)
    _write_meta(meta)
    return dest


def save_provider(cfg: dict) -> None:
    """Persist the user's LLM choice. `cfg` should have keys:
    provider, model, base_url (optional), label (optional)."""
    meta = _read_meta()
    meta["provider"] = {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "base_url": cfg.get("base_url"),
        "label": cfg.get("label"),
    }
    _write_meta(meta)


def get_provider() -> Optional[dict]:
    return _read_meta().get("provider")


def clear_provider() -> bool:
    meta = _read_meta()
    if "provider" in meta:
        del meta["provider"]
        _write_meta(meta)
        return True
    return False


def get_resume_path() -> Optional[Path]:
    meta = _read_meta()
    stored = meta.get("stored_path")
    if stored and Path(stored).exists():
        return Path(stored)
    # Fallback: any resume.* file in the config dir.
    for f in config_dir().glob("resume.*"):
        return f
    return None


def clear_resume() -> bool:
    found = False
    for f in config_dir().glob("resume.*"):
        f.unlink()
        found = True
    meta = _read_meta()
    if "original_path" in meta or "stored_path" in meta:
        meta.pop("original_path", None)
        meta.pop("stored_path", None)
        _write_meta(meta)
        found = True
    return found


def resume_info() -> Optional[dict]:
    path = get_resume_path()
    if not path:
        return None
    meta = _read_meta()
    return {
        "stored_path": str(path),
        "original_path": meta.get("original_path", "(unknown)"),
        "size_bytes": path.stat().st_size,
    }
