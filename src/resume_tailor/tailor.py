from .llm import LLMClient, strip_fences
from .prompts import SYSTEM_PROMPT, build_resume_block, build_user_prompt

_END_MARKER = "\\end{document}"


def _extract_tex_document(text: str) -> str:
    """Return the \\documentclass..\\end{document} span of the model output.
    Models occasionally wrap the document in prose despite instructions —
    salvage it when both markers are present, and fail here (rather than
    cryptically at compile time) when either is missing, which usually means
    a truncated completion."""
    start = text.find("\\documentclass")
    if start == -1:
        raise ValueError(
            "model returned no LaTeX document (no \\documentclass in "
            f"{len(text)} chars: {text[:60]!r}...)"
        )
    end = text.rfind(_END_MARKER)
    if end == -1:
        raise ValueError(
            "model output has \\documentclass but no \\end{document} — "
            "the completion was likely truncated"
        )
    return text[start:end + len(_END_MARKER)]


def tailor_resume(
    client: LLMClient,
    resume_text: str,
    jd_text: str,
    jd_url: str,
) -> str:
    """Generate a tailored LaTeX resume. The resume_text is sent as the
    cached/stable portion so providers that support prefix caching (Anthropic
    explicit, OpenAI automatic) only pay full price for it once per batch."""
    tex = client.complete(
        system=SYSTEM_PROMPT,
        cached_context=build_resume_block(resume_text),
        user=build_user_prompt(jd_text, jd_url),
    )
    return _extract_tex_document(strip_fences(tex))
