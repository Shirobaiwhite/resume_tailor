from .llm import LLMClient, strip_fences
from .prompts import SYSTEM_PROMPT, build_resume_block, build_user_prompt


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
    return strip_fences(tex).strip()
