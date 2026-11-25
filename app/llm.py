"""Provider-agnostic chat-model factory (Gemini / Groq / Ollama / offline fake) with fallbacks."""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from app.config import get_settings

log = logging.getLogger(__name__)


class ProviderNotConfigured(RuntimeError):
    pass


def build_chat_model(provider: str, model: str | None = None) -> BaseChatModel:
    s = get_settings()
    if provider == "gemini":
        if not s.gemini_api_key:
            raise ProviderNotConfigured(
                "GEMINI_API_KEY is not set (free key: https://aistudio.google.com/apikey)"
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model or s.gemini_model,
            google_api_key=s.gemini_api_key,
            temperature=s.llm_temperature,
            max_retries=s.llm_max_retries,
            timeout=s.llm_timeout_s,
            thinking_level=s.gemini_thinking_level,
        )
    if provider == "groq":
        if not s.groq_api_key:
            raise ProviderNotConfigured(
                "GROQ_API_KEY is not set (free key: https://console.groq.com/keys)"
            )
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model or s.groq_model,
            api_key=s.groq_api_key,
            temperature=s.llm_temperature,
            max_retries=s.llm_max_retries,
            timeout=s.llm_timeout_s,
        )
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=s.ollama_model, base_url=s.ollama_base_url, temperature=s.llm_temperature
        )
    if provider == "fake":
        from app.llm_fake import RuleBasedFakeChatModel

        return RuleBasedFakeChatModel()
    raise ValueError(f"Unknown LLM provider: {provider}")


def provider_chain() -> list[tuple[str, str | None]]:
    """Ordered (provider, model) pairs: the primary model, same-provider backup models
    (e.g. other free Gemini models when one is overloaded), then the fallback providers."""
    s = get_settings()
    chain: list[tuple[str, str | None]] = []
    for provider in [s.llm_provider] + [p for p in s.fallback_providers if p != s.llm_provider]:
        chain.append((provider, None))
        if provider == "gemini":
            chain += [("gemini", m) for m in s.gemini_backup_models if m != s.gemini_model]
    return chain


def build_tool_model(tools: list) -> Runnable:
    """Primary model bound to `tools`, falling back down the chain on errors (503 overload,
    429 rate limit, timeouts)."""
    bound: list[Runnable] = []
    for provider, model in provider_chain():
        try:
            bound.append(build_chat_model(provider, model).bind_tools(tools))
        except ProviderNotConfigured as e:
            if not bound:
                raise
            log.info("fallback provider %s skipped: %s", provider, e)
    primary, *fallbacks = bound
    return primary.with_fallbacks(fallbacks) if fallbacks else primary


def build_plain_model() -> BaseChatModel:
    """Model without tools (used by the LLM-as-judge in evals)."""
    return build_chat_model(get_settings().llm_provider)
