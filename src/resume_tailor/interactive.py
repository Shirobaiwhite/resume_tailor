"""Interactive walkthrough — gathers a resume, LLM provider, and JD URLs from
the user before any LLM call is made."""
import contextlib
import glob
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm import DEFAULT_MODELS, KNOWN_BASE_URLS
from .resume import load_resume
from .storage import get_provider, resume_info, save_provider, save_resume

MAX_JDS = 5

try:
    import readline  # stdlib on macOS/Linux
    _READLINE_AVAILABLE = True
except ImportError:
    _READLINE_AVAILABLE = False


@contextlib.contextmanager
def _path_completion():
    """Enable tab-completion for filesystem paths during an input() call.
    Restores the previous completer state on exit so other prompts (e.g. URLs)
    aren't affected."""
    if not _READLINE_AVAILABLE:
        yield
        return

    def completer(text: str, state: int):
        expanded = os.path.expanduser(text) if text else ""
        matches = glob.glob(expanded + "*")
        # Add trailing slash on directories so users can keep tabbing through.
        matches = [m + "/" if os.path.isdir(m) else m for m in matches]
        try:
            return matches[state]
        except IndexError:
            return None

    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()
    readline.set_completer(completer)
    # Default delims include "/" which would break path completion mid-string.
    readline.set_completer_delims(" \t\n")

    # macOS system Python uses libedit, which doesn't speak GNU readline's
    # bind syntax. Detect via the module docstring.
    doc = readline.__doc__ or ""
    if "libedit" in doc:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")

    try:
        yield
    finally:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)


def _yn(prompt: str, default: bool = False) -> bool:
    """Yes/no prompt. Empty input returns `default`."""
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            ans = input(prompt + suffix).strip().lower()
        except EOFError:
            return default
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  Please answer y or n.")


def prompt_resume() -> Optional[Path]:
    """Walk the user through providing or confirming a resume.
    Returns the path to use, or None if they bail out."""
    info = resume_info()

    if info:
        print(f"Resume imported: {info['original_path']}")
        if not _yn("Would you like to update it?", default=False):
            return Path(info["stored_path"])
        # Fall through to ask for a new path.

    while True:
        try:
            with _path_completion():
                entered = input("Please provide path to resume (Tab to autocomplete): ").strip()
        except EOFError:
            print()
            return None
        if not entered:
            print("  Aborted.")
            return None

        path = Path(entered).expanduser()
        if not path.exists():
            print(f"  File not found: {path}")
            continue

        try:
            text = load_resume(path)
        except ValueError as e:
            print(f"  {e}")
            continue

        if len(text) < 100:
            print(f"  Warning: extracted only {len(text)} characters.")
            if not _yn("  Use it anyway?", default=False):
                continue

        saved = save_resume(path)
        print(f"  Saved.")
        return saved


def _probe_url(url: str, timeout: float = 0.5) -> bool:
    import requests
    try:
        return requests.get(url, timeout=timeout).status_code < 500
    except Exception:
        return False


def _list_ollama_models() -> List[str]:
    import requests
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=1.0)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def detect_providers() -> List[Dict[str, Any]]:
    """Return every LLM option we can detect via env vars / running services."""
    options: List[Dict[str, Any]] = []

    if os.environ.get("ANTHROPIC_API_KEY"):
        options.append({
            "provider": "anthropic",
            "model": DEFAULT_MODELS["anthropic"],
            "base_url": None,
            "label": f"Anthropic ({DEFAULT_MODELS['anthropic']})",
            "detected_via": "ANTHROPIC_API_KEY env var",
        })
    if os.environ.get("OPENAI_API_KEY"):
        options.append({
            "provider": "openai",
            "model": DEFAULT_MODELS["openai"],
            "base_url": None,
            "label": f"OpenAI ({DEFAULT_MODELS['openai']})",
            "detected_via": "OPENAI_API_KEY env var",
        })
    if os.environ.get("DEEPSEEK_API_KEY"):
        options.append({
            "provider": "openai",
            "model": "deepseek-chat",
            "base_url": KNOWN_BASE_URLS["deepseek"],
            "label": "DeepSeek (deepseek-chat)",
            "detected_via": "DEEPSEEK_API_KEY env var",
        })
    if os.environ.get("GROQ_API_KEY"):
        options.append({
            "provider": "openai",
            "model": "llama-3.3-70b-versatile",
            "base_url": KNOWN_BASE_URLS["groq"],
            "label": "Groq (llama-3.3-70b-versatile)",
            "detected_via": "GROQ_API_KEY env var",
        })
    if os.environ.get("OPENROUTER_API_KEY"):
        options.append({
            "provider": "openai",
            "model": "anthropic/claude-opus-4",
            "base_url": KNOWN_BASE_URLS["openrouter"],
            "label": "OpenRouter (anthropic/claude-opus-4)",
            "detected_via": "OPENROUTER_API_KEY env var",
        })
    if os.environ.get("TOGETHER_API_KEY"):
        options.append({
            "provider": "openai",
            "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "base_url": KNOWN_BASE_URLS["together"],
            "label": "Together (Llama-3.3-70B)",
            "detected_via": "TOGETHER_API_KEY env var",
        })
    if os.environ.get("GEMINI_API_KEY"):
        options.append({
            "provider": "openai",
            "model": "gemini-2.0-flash",
            "base_url": KNOWN_BASE_URLS["gemini"],
            "label": "Google Gemini (gemini-2.0-flash)",
            "detected_via": "GEMINI_API_KEY env var",
        })
    if os.environ.get("GITHUB_TOKEN"):
        options.append({
            "provider": "openai",
            "model": "gpt-4o-mini",
            "base_url": KNOWN_BASE_URLS["github"],
            "label": "GitHub Models (gpt-4o-mini)",
            "detected_via": "GITHUB_TOKEN env var",
        })

    # Local servers — quick probe.
    if _probe_url("http://localhost:11434/api/tags"):
        models = _list_ollama_models()
        if models:
            model = models[0]
            options.append({
                "provider": "openai",
                "model": model,
                "base_url": KNOWN_BASE_URLS["ollama"],
                "label": f"Ollama ({model})",
                "detected_via": "running at localhost:11434",
            })
    if _probe_url("http://localhost:1234/v1/models"):
        options.append({
            "provider": "openai",
            "model": "loaded-model",
            "base_url": KNOWN_BASE_URLS["lmstudio"],
            "label": "LM Studio (whatever's loaded)",
            "detected_via": "running at localhost:1234",
        })

    return options


def _print_setup_help() -> None:
    print()
    print("Get an API key from one of these and set it in your shell:")
    print()
    print("  Anthropic    https://console.anthropic.com/settings/keys")
    print("    export ANTHROPIC_API_KEY=sk-ant-...")
    print()
    print("  OpenAI       https://platform.openai.com/api-keys")
    print("    export OPENAI_API_KEY=sk-...")
    print()
    print("  DeepSeek     https://platform.deepseek.com/api_keys")
    print("    export DEEPSEEK_API_KEY=sk-...")
    print()
    print("  Groq (free)  https://console.groq.com/keys")
    print("    export GROQ_API_KEY=gsk_...")
    print()
    print("Or run a local model — no API key needed:")
    print("  Ollama:      https://ollama.com  →  ollama pull llama3.1  →  ollama serve")
    print("  LM Studio:   https://lmstudio.ai  (start the local server)")
    print()
    print("Then re-run ./run.")


def _prompt_custom_provider() -> Optional[Dict[str, Any]]:
    print("\nCustom provider setup:")
    try:
        provider = input("  Provider type (anthropic/openai) [openai]: ").strip() or "openai"
        if provider not in {"anthropic", "openai"}:
            print(f"  Unknown provider: {provider}")
            return None
        base_url = input("  Base URL (blank for default): ").strip() or None
        model = input("  Model ID: ").strip()
        if not model:
            print("  Model is required.")
            return None
    except EOFError:
        return None
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "label": f"{model}" + (f" @ {base_url}" if base_url else ""),
    }


def prompt_provider() -> Optional[Dict[str, Any]]:
    """Walk the user through choosing/setting up an LLM provider.
    Returns the provider config dict, or None if aborted."""
    stored = get_provider()
    if stored:
        label = stored.get("label") or f"{stored['provider']} / {stored['model']}"
        print(f"LLM: {label}")
        if not _yn("Would you like to change it?", default=False):
            return stored

    available = detect_providers()

    print()
    if not available:
        print("No LLM provider detected.")
        print("  No API keys set, and no local server (Ollama / LM Studio) running.")
        if _yn("Show setup instructions?", default=True):
            _print_setup_help()
        if _yn("Enter a provider manually now?", default=False):
            chosen = _prompt_custom_provider()
            if chosen:
                save_provider(chosen)
            return chosen
        return None

    print("Choose an LLM provider:")
    for i, opt in enumerate(available, 1):
        print(f"  {i}) {opt['label']}  ({opt['detected_via']})")
    custom_idx = len(available) + 1
    print(f"  {custom_idx}) Enter a custom provider/model")

    while True:
        try:
            raw = input("> ").strip()
        except EOFError:
            return None
        if not raw:
            continue
        try:
            idx = int(raw)
        except ValueError:
            print(f"  Enter a number 1-{custom_idx}.")
            continue
        if 1 <= idx <= len(available):
            chosen = available[idx - 1]
            # Strip detection metadata before persisting.
            to_save = {k: v for k, v in chosen.items() if k != "detected_via"}
            save_provider(to_save)
            return to_save
        if idx == custom_idx:
            chosen = _prompt_custom_provider()
            if chosen:
                save_provider(chosen)
            return chosen
        print(f"  Enter a number 1-{custom_idx}.")


def prompt_jds() -> List[str]:
    """Collect up to MAX_JDS JD URLs from the user. Empty line ends input
    once at least one URL has been entered; auto-finishes at the cap."""
    print()
    print(f"Paste up to {MAX_JDS} job description URLs (one per line).")
    print("Press Enter on a blank line when you're done.")
    urls: List[str] = []
    while len(urls) < MAX_JDS:
        try:
            line = input(f"  URL [{len(urls) + 1}/{MAX_JDS}]: ").strip()
        except EOFError:
            print()
            break
        if not line:
            if urls:
                break
            print("  Need at least one URL — paste one, or Ctrl-C to abort.")
            continue
        if not (line.startswith("http://") or line.startswith("https://")):
            print("  That doesn't look like a URL. Skipped.")
            continue
        urls.append(line)
    return urls


def run_interactive() -> Optional[dict]:
    """Run the interactive flow. Returns a dict of gathered inputs,
    or None if the user aborted."""
    print("resume-tailor — interactive mode")
    print("--------------------------------")

    try:
        resume_path = prompt_resume()
        if not resume_path:
            return None

        provider = prompt_provider()
        if not provider:
            return None

        urls = prompt_jds()
        if not urls:
            print("No URLs provided. Nothing to do.", file=sys.stderr)
            return None
    except KeyboardInterrupt:
        print("\nAborted.")
        return None

    return {"resume_path": resume_path, "urls": urls, "provider": provider}
