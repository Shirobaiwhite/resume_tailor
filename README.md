# resume-tailor

A CLI that tailors a single source resume to a list of job descriptions using an LLM of your choice — Claude, GPT, Gemini, Groq, DeepSeek, a local model via Ollama or LM Studio, anything OpenAI-compatible. For each JD URL you pass in, it produces a tailored LaTeX resume (and a compiled PDF if you have a LaTeX toolchain installed).

The tailoring is constrained: the model is instructed to rephrase, reorder, and emphasize content from your real resume — never to fabricate experience, skills, employers, dates, or accomplishments.

## Quick start

```bash
git clone <this repo>
cd resume_tailor
./run
```

The `./run` script bootstraps a Python venv on first invocation (~30 seconds) and then launches an interactive walkthrough. No flags, no setup.

On first run you'll see:

```
resume-tailor — interactive mode
--------------------------------
Please provide path to resume (Tab to autocomplete): _
```

Then it asks which LLM provider to use (detected from your shell env), and finally collects up to 5 JD URLs. Press Enter on a blank line when you're done. You'll get a confirmation before any API calls are made.

## What it produces

For each JD, a directory under `./tailored/`:

```
tailored/
├── stripe-com-jobs-senior-engineer/
│   ├── jd.txt        # extracted JD text (or what you pasted)
│   ├── resume.tex    # tailored LaTeX source
│   └── resume.pdf    # compiled PDF (if LaTeX is installed)
└── anthropic-com-careers-research-engineer/
    └── ...
```

Without LaTeX installed you get `.tex` only — paste into [Overleaf](https://overleaf.com) to render, or install a TeX distribution (below) to compile locally.

## How it works

1. **Resume** is loaded once and saved to `~/.config/resume-tailor/` for future runs.
2. **LLM provider** is auto-detected from your shell env (API keys + local servers) and saved for future runs.
3. **JD URLs** — for each one:
   - Try the **Greenhouse API** directly if the URL contains `gh_jid` or is on `boards.greenhouse.io`. Bypasses JavaScript-rendered pages entirely.
   - Hand the raw page text to your **LLM** and ask it to extract just the JD content (drops company boilerplate, perks, EEO statements).
   - Fall back to **CSS-selector extraction** (`<main>`, `<article>`, Lever/Workday/LinkedIn-specific containers).
   - Fall back to **raw stripped text**.
   - Show you a preview and ask if it looks right. If you reject all automated strategies, you're prompted to paste manually.
4. **Tailor** — the resume + the confirmed JD go to the LLM, which returns a complete `.tex` document.
5. **Compile** to PDF if `pdflatex` or `xelatex` is on your `PATH`.

The resume content is sent as the stable prefix of every request. Anthropic explicit caching and OpenAI automatic prefix caching both kick in across a batch — second JD onward is much cheaper.

## Install

The `./run` script handles install automatically. If you want to do it manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Requires Python 3.9+.

### Optional: LaTeX → PDF

To compile to PDF locally:

- macOS (small): `brew install --cask basictex` then restart your shell
- macOS (full): `brew install --cask mactex`
- Linux: `sudo apt install texlive-latex-recommended texlive-fonts-recommended`

## Choose a backend

`./run` will auto-detect what's available in your shell. Set one of these env vars and the matching provider appears in the picker:

| Provider | Env var | Notes |
|---|---|---|
| Anthropic Claude | `ANTHROPIC_API_KEY` | Uses prompt caching + adaptive thinking |
| OpenAI | `OPENAI_API_KEY` | GPT-4o by default |
| Google Gemini | `GEMINI_API_KEY` | Free tier: 15 RPM / 1M tokens per day |
| Groq | `GROQ_API_KEY` | Free tier: Llama-3.3-70b, very fast |
| DeepSeek | `DEEPSEEK_API_KEY` | Cheap, OpenAI-compatible |
| OpenRouter | `OPENROUTER_API_KEY` | Routed access to many models |
| Together | `TOGETHER_API_KEY` | Llama-3.3-70b, open-weights models |
| GitHub Models | `GITHUB_TOKEN` | Free for personal use |
| Ollama (local) | none | Auto-detected at `localhost:11434` |
| LM Studio (local) | none | Auto-detected at `localhost:1234` |

You can also pick "Custom" in the interactive picker to enter any OpenAI-compatible `base_url` + model.

## Flag mode (skip interactive)

Power users can bypass the walkthrough:

```bash
# Save a resume as default, then use it
./run -r resume.pdf
./run -j https://example.com/job/1 https://example.com/job/2

# One-shot with explicit provider
./run -r resume.pdf -j URL --provider openai --model gpt-4o

# Local model via preset
./run -j URL --provider-preset ollama --model llama3.1

# Custom base URL
./run -j URL --base-url http://localhost:8000/v1 --model my-model
```

### All flags

| Flag | Description |
|---|---|
| `-r`, `--resume` | Path to source resume — `.pdf`, `.md`, or `.txt`. Saved as default unless `--no-save`. |
| `--no-save` | Use `--resume` once without saving it as the new default. |
| `--show-resume` | Show info about the saved default resume and exit. |
| `--clear-resume` | Forget the saved default resume and exit. |
| `--show-llm` | Show the saved LLM provider/model and exit. |
| `--clear-llm` | Forget the saved LLM provider and exit. |
| `-j`, `--jds` | One or more job description URLs (max 5). |
| `-o`, `--out` | Output directory. Default: `./tailored`. |
| `--no-pdf` | Skip PDF compilation even if a LaTeX compiler is installed. |
| `--provider` | `anthropic` or `openai`. Inferred when `--base-url` or `--provider-preset` is set. |
| `-m`, `--model` | Model ID. |
| `--base-url` | OpenAI-compatible API base URL. |
| `--provider-preset` | Shortcut for a known base URL: `deepseek`, `groq`, `openrouter`, `together`, `gemini`, `github`, `ollama`, `lmstudio`. |
| `--api-key-env` | Env var name to read the API key from. |

## JD extraction & confirmation

For each URL, the program tries up to five extraction strategies in order:

1. **Greenhouse API** — direct JSON fetch when `gh_jid` is in the URL. Avoids JS-rendered pages entirely.
2. **LLM-extracted** — feeds the raw page text to your LLM with a "keep only the JD" prompt. Robust across thousands of careers sites.
3. **article-focused** — CSS selectors for `<main>`, `<article>`, `[itemprop=description]`, Lever's `.posting-content`, LinkedIn's `.show-more-less-html__markup`, Workday's `[data-automation-id*=description]`.
4. **standard** — strip nav/header/footer/scripts, return all body text.
5. **googlebot UA** — only if every other strategy returned nothing.

Each result is shown in a preview (full text if ≤3000 chars, head + tail otherwise) and you confirm with `[Y/n]`. Reject all → polite paste prompt.

## What gets tailored, and what doesn't

The system prompt instructs the model to:

- Rephrase, reorder, and emphasize content from your real resume to match the JD's language and priorities
- Match terminology (e.g., "k8s" → "Kubernetes" if the JD uses that)
- Cut content that's clearly irrelevant to the target role rather than padding
- Keep to roughly one page

And explicitly forbids:

- Inventing experience, skills, employers, dates, degrees, or accomplishments
- Adding quantified impact numbers the source resume doesn't already contain

The output is a self-contained `.tex` file using only the standard `article` class plus `geometry`, `hyperref`, `enumitem`, `titlesec`, and `xcolor` — no external `.cls` files, compiles anywhere with a basic TeX Live install.

## Cost notes

| Provider | Per JD | 5 JDs |
|---|---|---|
| Claude Opus 4.7 | ~$0.15-0.20 | ~$0.75-1.00 |
| Claude Sonnet 4.6 | ~$0.07-0.10 | ~$0.35-0.50 |
| Claude Haiku 4.5 | ~$0.02-0.03 | ~$0.10-0.15 |
| GPT-4o | ~$0.05-0.08 | ~$0.25-0.40 |
| GPT-4o-mini | ~$0.005 | ~$0.025 |
| Gemini 2.0 Flash (free tier) | $0 | $0 |
| Groq Llama-3.3-70b (free tier) | $0 | $0 |
| Ollama (local) | $0 | $0 |

Each JD costs ~2 API calls (one for extraction, one for tailoring). Resume tokens are cached across JDs in a batch, so the second JD onward is significantly cheaper.

## Configuration files

```
~/.config/resume-tailor/
├── resume.pdf       # copy of your saved resume
└── config.json      # saved provider + resume metadata
```

Honors `$XDG_CONFIG_HOME` if set. Use `--show-resume` / `--show-llm` to inspect, `--clear-resume` / `--clear-llm` to forget.

