"""Application settings, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["gemini", "groq", "ollama", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"

    # --- Database -----------------------------------------------------------------
    database_url: str = "postgresql://ops:ops@localhost:55432/ops_agent"
    # Least-privilege role used ONLY by the run_sql_query tool (SELECT on allow-listed tables).
    readonly_db_user: str = "agent_readonly"
    readonly_db_password: str = "agent_readonly_pw"

    # --- LLM providers (all free tiers) --------------------------------------------
    llm_provider: Provider = "gemini"
    # Comma-separated list tried in order when the primary fails (rate limit, outage...).
    llm_fallbacks: str = "groq"
    llm_temperature: float = 0.0
    llm_max_retries: int = 1
    llm_timeout_s: float = 60.0

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_thinking_level: Literal["minimal", "low", "medium", "high"] | None = "low"
    # Other free Gemini models tried (in order) when the primary is overloaded / rate limited.
    gemini_backup_models_csv: str = Field(
        default="gemini-3.6-flash,gemini-3.8-flash", alias="GEMINI_BACKUP_MODELS"
    )

    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"

    # Optional price table (USD per 1M tokens) for cost tracking. Free tiers cost $0, but you
    # can set list prices here to estimate what the same traffic would cost on a paid plan.
    price_input_per_mtok: float = 0.0
    price_output_per_mtok: float = 0.0

    # --- Embeddings / retrieval -----------------------------------------------------
    embedding_provider: Literal["fastembed", "hash"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    embedding_cache_dir: str | None = None
    kb_dir: str = "data/kb"
    retrieval_top_k: int = 4

    # --- Agent behaviour / safety -----------------------------------------------------
    max_agent_steps: int = 10
    approval_mode: Literal["risk_based", "all_writes"] = "risk_based"
    refund_max_usd: float = 500.0
    sql_row_limit: int = 50
    sql_timeout_ms: int = 3000
    max_input_chars: int = 4000
    # Browser origins allowed to call the API (the Next.js operator console).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Public-demo protection: max LLM-spending requests per client IP per hour (0 = unlimited).
    demo_rate_limit_per_hour: int = 0
    allowed_sql_tables: list[str] = Field(
        default_factory=lambda: [
            "plans",
            "customers",
            "subscriptions",
            "invoices",
            "tickets",
            "ticket_events",
            "refunds",
        ]
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def gemini_backup_models(self) -> list[str]:
        return [m.strip() for m in self.gemini_backup_models_csv.split(",") if m.strip()]

    @property
    def fallback_providers(self) -> list[str]:
        return [p.strip() for p in self.llm_fallbacks.split(",") if p.strip()]

    @property
    def readonly_database_url(self) -> str:
        params = conninfo_to_dict(self.database_url)
        params["user"] = self.readonly_db_user
        params["password"] = self.readonly_db_password
        return make_conninfo(**params)


@lru_cache
def get_settings() -> Settings:
    return Settings()
