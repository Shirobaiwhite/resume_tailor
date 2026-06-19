import argparse
import sys
from pathlib import Path
from typing import Optional  # noqa: F401  (used by Optional[ProviderConfig])

from .interactive import MAX_JDS, _yn, run_interactive
import json

from .jd import fetch_jd
from .latex import compile_tex, find_latex_compiler, offer_to_install_tectonic
from .score import MatchScore, POOR_FIT_THRESHOLD, score_match
from .llm import DEFAULT_MODELS, KNOWN_BASE_URLS, ProviderConfig, build_client
from .resume import load_resume
from .storage import (
    clear_provider,
    clear_resume,
    get_provider,
    get_resume_path,
    resume_info,
    save_resume,
)
from .tailor import tailor_resume


# Below this match score (0-100), prompt before paying for tailoring.
# Defaults to the "Poor fit" boundary from the scoring bands; overridable
# per-run with --min-score.
DEFAULT_MIN_MATCH_SCORE = POOR_FIT_THRESHOLD


def _print_match(match: MatchScore) -> None:
    """Pretty-print a match score with a unicode bar + strengths/gaps."""
    bar_filled = round(match.score / 5)  # 0-100 → 0-20 cells
    bar = "█" * bar_filled + "░" * (20 - bar_filled)
    print()
    print(f"  Match score: {match.score}/100 — {match.verdict()}")
    print(f"  [{bar}]")
    if match.reason:
        print(f"  Why: {match.reason}")
    if match.strengths:
        print("  Strengths:")
        for s in match.strengths:
            print(f"    + {s}")
    if match.gaps:
        print("  Gaps:")
        for g in match.gaps:
            print(f"    - {g}")


def _make_jd_confirmer():
    """Build a callback that previews extracted JD text and asks the user
    if it looks right. Used by fetch_jd() in interactive/TTY mode."""
    # If the extracted text is short enough to skim, show all of it.
    # Otherwise show a generous head + tail so the user can sanity-check both.
    FULL_BELOW = 3000
    HEAD_CHARS = 2000
    TAIL_CHARS = 500

    def confirm(text: str, method: str) -> bool:
        if len(text) <= FULL_BELOW:
            preview = text
            note = f"({len(text)} chars)"
        else:
            preview = (
                text[:HEAD_CHARS]
                + f"\n\n  ... [skipped {len(text) - HEAD_CHARS - TAIL_CHARS} chars] ...\n\n"
                + text[-TAIL_CHARS:]
            )
            note = f"({len(text)} chars total, showing head + tail)"
        print()
        print(f"  Extracted via {method} method {note}:")
        print("  ----------------------------------------")
        for line in preview.splitlines():
            print(f"  {line}")
        print("  ----------------------------------------")
        return _yn("  Does this look like the right job description?", default=True)
    return confirm


def _resolve_provider(args: argparse.Namespace) -> ProviderConfig:
    base_url = args.base_url
    if args.provider_preset:
        if args.provider_preset not in KNOWN_BASE_URLS:
            raise SystemExit(
                f"Unknown preset: {args.provider_preset!r}. "
                f"Known: {', '.join(KNOWN_BASE_URLS)}"
            )
        base_url = base_url or KNOWN_BASE_URLS[args.provider_preset]

    # Fall back to the saved provider when no LLM flags were given.
    if not (args.provider or args.model or base_url or args.api_key_env):
        stored = get_provider()
        if stored:
            return ProviderConfig(
                provider=stored["provider"],
                model=stored["model"],
                base_url=stored.get("base_url"),
                api_key_env=args.api_key_env,
            )

    provider = args.provider
    if not provider:
        provider = "openai" if base_url else "anthropic"

    if provider not in {"anthropic", "openai"}:
        raise SystemExit(
            f"--provider must be 'anthropic' or 'openai', got {provider!r}."
        )

    model = args.model or DEFAULT_MODELS[provider]

    return ProviderConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key_env=args.api_key_env,
    )


def _resolve_resume(args: argparse.Namespace) -> Optional[Path]:
    """Pick a resume path. Priority: --resume > stored default > interactive prompt.
    When --resume is given, it's saved as the new default unless --no-save is set."""
    if args.resume:
        if not args.resume.exists():
            print(f"Resume not found: {args.resume}", file=sys.stderr)
            return None
        if args.no_save:
            return args.resume
        saved = save_resume(args.resume)
        print(f"Saved resume as default: {saved}")
        return saved

    stored = get_resume_path()
    if stored:
        return stored

    if not sys.stdin.isatty():
        print(
            "No resume saved. Provide --resume PATH or run interactively.",
            file=sys.stderr,
        )
        return None

    entered = input(
        "No resume saved yet. Path to your resume (.pdf/.md/.txt): "
    ).strip()
    if not entered:
        print("Aborted.", file=sys.stderr)
        return None
    path = Path(entered).expanduser()
    if not path.exists():
        print(f"Resume not found: {path}", file=sys.stderr)
        return None
    saved = save_resume(path)
    print(f"Saved resume as default: {saved}")
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="resume-tailor",
        description="Tailor a resume to a list of job descriptions using an LLM.",
    )
    parser.add_argument(
        "--resume", "-r", type=Path,
        help="Path to the source resume (.pdf, .md, or .txt). "
        "Saved as the default for future runs. Omit to use the saved resume.",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Don't save --resume as the default — use it once and forget.",
    )
    parser.add_argument(
        "--show-resume", action="store_true",
        help="Show info about the saved default resume and exit.",
    )
    parser.add_argument(
        "--clear-resume", action="store_true",
        help="Forget the saved default resume and exit.",
    )
    parser.add_argument(
        "--show-llm", action="store_true",
        help="Show the saved LLM provider/model and exit.",
    )
    parser.add_argument(
        "--clear-llm", action="store_true",
        help="Forget the saved LLM provider and exit.",
    )
    parser.add_argument(
        "--jds", "-j", nargs="+", metavar="URL",
        help="One or more job description URLs.",
    )
    parser.add_argument(
        "--out", "-o", type=Path, default=Path("./tailored"),
        help="Output directory (default: ./tailored).",
    )
    parser.add_argument(
        "--no-pdf", action="store_true",
        help="Skip PDF compilation even if a LaTeX compiler is installed.",
    )
    parser.add_argument(
        "--min-score", type=int, default=DEFAULT_MIN_MATCH_SCORE, metavar="N",
        help="Match score (0-100) below which to prompt before tailoring "
        f"(default: {DEFAULT_MIN_MATCH_SCORE}).",
    )

    llm = parser.add_argument_group("LLM backend")
    llm.add_argument(
        "--provider", choices=["anthropic", "openai"],
        help="LLM backend. 'openai' covers any OpenAI-compatible endpoint "
        "(OpenAI, DeepSeek, Ollama, LM Studio, vLLM, ...). "
        "Inferred from --base-url / --provider-preset when omitted.",
    )
    llm.add_argument(
        "--model", "-m",
        help=f"Model ID. Defaults: anthropic={DEFAULT_MODELS['anthropic']}, "
        f"openai={DEFAULT_MODELS['openai']}.",
    )
    llm.add_argument(
        "--base-url",
        help="OpenAI-compatible API base URL.",
    )
    llm.add_argument(
        "--provider-preset", choices=sorted(KNOWN_BASE_URLS),
        help="Shortcut for a known --base-url: "
        + ", ".join(sorted(KNOWN_BASE_URLS)) + ".",
    )
    llm.add_argument(
        "--api-key-env",
        help="Env var name to read the API key from. Defaults: "
        "ANTHROPIC_API_KEY (anthropic), OPENAI_API_KEY (openai).",
    )

    args = parser.parse_args()

    # Management commands — handled before anything else.
    if args.show_resume:
        info = resume_info()
        if info:
            print(f"Saved resume: {info['stored_path']}")
            print(f"  source: {info['original_path']}")
            print(f"  size:   {info['size_bytes']} bytes")
        else:
            print("No resume saved.")
        return 0

    if args.clear_resume:
        if clear_resume():
            print("Cleared saved resume.")
        else:
            print("No resume to clear.")
        return 0

    if args.show_llm:
        p = get_provider()
        if p:
            label = p.get("label") or f"{p['provider']} / {p['model']}"
            print(f"Saved LLM: {label}")
            if p.get("base_url"):
                print(f"  base_url: {p['base_url']}")
        else:
            print("No LLM provider saved.")
        return 0

    if args.clear_llm:
        if clear_provider():
            print("Cleared saved LLM.")
        else:
            print("No LLM to clear.")
        return 0

    # Bare invocation (no flags) → interactive walkthrough.
    interactive_mode = (
        args.resume is None
        and not args.jds
        and not args.no_save
        and sys.stdin.isatty()
    )

    cfg: Optional[ProviderConfig] = None
    if interactive_mode:
        gathered = run_interactive()
        if not gathered:
            return 1
        resume_path = gathered["resume_path"]
        jd_urls = gathered["urls"]
        p = gathered["provider"]
        cfg = ProviderConfig(
            provider=p["provider"],
            model=p["model"],
            base_url=p.get("base_url"),
            api_key_env=args.api_key_env,
        )
        print()
        print(f"Ready to tailor against {len(jd_urls)} JD(s).")
        if not _yn("Continue?", default=True):
            print("Aborted.")
            return 0
    else:
        resume_path = _resolve_resume(args)
        if resume_path is None:
            return 1
        if not args.jds:
            if args.resume:
                print("\nResume saved. Pass --jds URL ... to tailor it.")
                return 0
            print("Nothing to do — pass --jds URL ... to tailor.", file=sys.stderr)
            return 1
        jd_urls = args.jds[:MAX_JDS]
        if len(args.jds) > MAX_JDS:
            print(
                f"Got {len(args.jds)} URLs; capping at {MAX_JDS}. "
                f"Dropping: {args.jds[MAX_JDS:]}",
                file=sys.stderr,
            )

    if cfg is None:
        cfg = _resolve_provider(args)
    try:
        client = build_client(cfg)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    label = cfg.model
    if cfg.base_url:
        label = f"{cfg.model} @ {cfg.base_url}"
    print(f"Using {cfg.provider}: {label}")

    print(f"Loading resume from {resume_path}...")
    resume_text = load_resume(resume_path)
    if len(resume_text) < 100:
        print(
            f"Warning: extracted resume is only {len(resume_text)} chars. "
            "Check the source file.",
            file=sys.stderr,
        )

    compiler = None if args.no_pdf else find_latex_compiler()
    if compiler:
        print(f"Found LaTeX compiler: {compiler}")
    elif not args.no_pdf:
        # Offer to auto-install (interactive mode + macOS only).
        if sys.stdin.isatty():
            compiler = offer_to_install_tectonic()
        if not compiler:
            print("No LaTeX compiler found on PATH; emitting .tex only.")

    args.out.mkdir(parents=True, exist_ok=True)

    confirm_cb = _make_jd_confirmer() if sys.stdin.isatty() else None

    succeeded = 0
    results: list = []  # for the end-of-run summary
    for i, url in enumerate(jd_urls, 1):
        print(f"\n[{i}/{len(jd_urls)}] {url}")
        jd = fetch_jd(url, confirm=confirm_cb, llm_client=client)
        if not jd.text:
            print("  skipped: no JD text available.", file=sys.stderr)
            continue
        print(f"  JD: {len(jd.text)} chars")

        # Match-score before tailoring so the user knows what they're getting.
        match: Optional[MatchScore] = None
        try:
            print("  Scoring resume against JD...")
            match = score_match(client, resume_text, jd.text, jd.url)
            _print_match(match)
        except Exception as e:
            print(f"  (couldn't compute match score: {e})", file=sys.stderr)

        # On very poor matches, give the user an out before paying for tailoring.
        if (
            match is not None
            and match.score < args.min_score
            and sys.stdin.isatty()
        ):
            print()
            if not _yn(
                f"  Match is only {match.score}/100. Tailor anyway?",
                default=False,
            ):
                print("  Skipped.")
                results.append((jd.slug, match, "skipped"))
                continue

        try:
            tex = tailor_resume(client, resume_text, jd.text, jd.url)
        except Exception as e:
            print(f"  failed: {e}", file=sys.stderr)
            results.append((jd.slug, match, "failed"))
            continue

        job_dir = args.out / jd.slug
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "jd.txt").write_text(jd.text, encoding="utf-8")
        tex_path = job_dir / "resume.tex"
        tex_path.write_text(tex, encoding="utf-8")
        print(f"  wrote {tex_path}")

        if match is not None:
            (job_dir / "match.json").write_text(
                json.dumps(match.to_dict(), indent=2), encoding="utf-8"
            )

        if compiler:
            pdf_path = compile_tex(tex_path, compiler)
            if pdf_path:
                print(f"  wrote {pdf_path}")

        succeeded += 1
        results.append((jd.slug, match, "ok"))

    print(f"\nDone. {succeeded}/{len(jd_urls)} resumes generated in {args.out}/")
    if any(m is not None for _, m, _ in results):
        print("\nMatch summary:")
        for slug, m, status in results:
            score_str = f"{m.score:>3}/100" if m else "  -/100"
            verdict = m.verdict() if m else "no score"
            tag = "" if status == "ok" else f" [{status}]"
            print(f"  {score_str}  {verdict:<18}  {slug}{tag}")

    return 0 if succeeded > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
