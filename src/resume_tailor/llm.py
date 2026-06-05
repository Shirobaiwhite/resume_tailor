"""Pluggable LLM backends. Two providers cover effectively everything:

- anthropic: Claude models, with prompt caching + adaptive thinking.
- openai:    Any OpenAI-compatible endpoint. Covers OpenAI itself, DeepSeek,
             Together, OpenRouter, Groq, Ollama, LM Studio, llama.cpp server,
             vLLM, etc. — just point --base-url at them.
"""
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional, Protocol


class LLMClient(Protocol):
    def complete(self, system: str, cached_context: str, user: str) -> str:
        """Return the model's text completion."""
        ...


@dataclass
class ProviderConfig:
    provider: str           # "anthropic" or "openai"
    model: str
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None  # name of the env var holding the key


# Default models when --model isn't given.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-7",
    "openai": "gpt-4o",
}

# Hostname-based base-url presets, for the curious. Users can also just pass --base-url directly.
KNOWN_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "github": "https://models.inference.ai.azure.com",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}

# Default env var to read an API key from, when the user doesn't specify one.
DEFAULT_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def resolve_api_key(cfg: ProviderConfig) -> Optional[str]:
    """Look up the API key from env. Local endpoints (localhost) don't need one."""
    if cfg.api_key_env:
        return os.environ.get(cfg.api_key_env)
    if cfg.provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    # openai: try provider-specific envs first based on base_url, then fall back.
    if cfg.base_url:
        host = cfg.base_url.lower()
        if "localhost" in host or "127.0.0.1" in host:
            return "local"  # placeholder; openai SDK requires a non-empty string
        for hint, env_var in [
            ("deepseek", "DEEPSEEK_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
            ("groq", "GROQ_API_KEY"),
            ("together", "TOGETHER_API_KEY"),
            ("generativelanguage.googleapis.com", "GEMINI_API_KEY"),
            ("models.inference.ai.azure.com", "GITHUB_TOKEN"),
        ]:
            if hint in host and os.environ.get(env_var):
                return os.environ[env_var]
    return os.environ.get("OPENAI_API_KEY")


def build_client(cfg: ProviderConfig) -> LLMClient:
    api_key = resolve_api_key(cfg)
    if cfg.provider == "anthropic":
        if not api_key:
            raise RuntimeError(
                "No API key for Anthropic. Set ANTHROPIC_API_KEY or use "
                "--api-key-env to point at a different variable."
            )
        return _AnthropicLLM(model=cfg.model, api_key=api_key)

    if cfg.provider == "openai":
        if not api_key:
            base = cfg.base_url or "https://api.openai.com/v1"
            raise RuntimeError(
                f"No API key found for {base}. Set OPENAI_API_KEY (or the "
                "provider-specific env var) or pass --api-key-env."
            )
        return _OpenAICompatibleLLM(
            model=cfg.model, base_url=cfg.base_url, api_key=api_key
        )

    raise ValueError(f"Unknown provider: {cfg.provider!r}")


class _AnthropicLLM:
    def __init__(self, model: str, api_key: str):
        import anthropic
        self.anthropic = anthropic
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, cached_context: str, user: str) -> str:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[
                {"type": "text", "text": system},
                {
                    "type": "text",
                    "text": cached_context,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": user}],
        ) as stream:
            message = stream.get_final_message()
        return "".join(b.text for b in message.content if b.type == "text")


class _OpenAICompatibleLLM:
    def __init__(self, model: str, base_url: Optional[str], api_key: str):
        import openai
        self.model = model
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key)

    def complete(self, system: str, cached_context: str, user: str) -> str:
        # OpenAI-compatible endpoints don't share Anthropic's explicit caching
        # API. (OpenAI itself does automatic prefix caching, so putting the
        # stable content first still helps.) Concatenate into one system msg.
        full_system = f"{system}\n\n{cached_context}"
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": user},
                ],
                max_tokens=8192,
            )
        except Exception as e:
            # The openai SDK raises specific exceptions, but local servers
            # (Ollama etc.) often surface different shapes — keep this broad.
            print(f"  LLM error: {e}", file=sys.stderr)
            raise
        return response.choices[0].message.content or ""


def strip_fences(text: str) -> str:
    """Some non-Claude models like to wrap output in ```latex ... ``` despite instructions."""
    match = re.search(r"```(?:latex|tex)?\s*\n(.*?)\n```", text, re.DOTALL)
    return match.group(1) if match else text
