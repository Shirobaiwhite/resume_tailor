"""Match-score the resume against a JD before tailoring. Lets the user see
how strong the fit is and bail out early on poor matches."""
import json
import re
from dataclasses import dataclass, asdict
from typing import List

from .llm import LLMClient
from .prompts import MATCH_SCORE_SYSTEM, build_match_user_prompt, build_resume_block


@dataclass
class MatchScore:
    score: int  # 0-100
    reason: str
    strengths: List[str]
    gaps: List[str]

    def verdict(self) -> str:
        if self.score >= 90:
            return "Exceptional fit"
        if self.score >= 75:
            return "Strong fit"
        if self.score >= 60:
            return "Reasonable fit"
        if self.score >= 40:
            return "Stretch"
        return "Poor fit"

    def to_dict(self) -> dict:
        return asdict(self)


def score_match(
    client: LLMClient,
    resume_text: str,
    jd_text: str,
    jd_url: str,
) -> MatchScore:
    """Ask the LLM to score the resume against the JD. The resume text is
    sent as the cached context (same key as the tailoring call), so the
    cache hit covers both calls."""
    raw = client.complete(
        system=MATCH_SCORE_SYSTEM,
        cached_context=build_resume_block(resume_text),
        user=build_match_user_prompt(jd_text, jd_url),
    )
    data = _parse_score_json(raw)
    return MatchScore(
        score=_clamp_score(data.get("score", 0)),
        reason=str(data.get("reason", "")).strip(),
        strengths=_as_str_list(data.get("strengths")),
        gaps=_as_str_list(data.get("gaps")),
    )


def _parse_score_json(raw: str) -> dict:
    text = raw.strip()
    # Strip markdown fences if the model added them despite instructions.
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last-resort: pull the first {...} block out of any commentary.
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _clamp_score(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _as_str_list(value) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
