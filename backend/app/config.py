"""Application settings. Every secret comes from environment variables / .env (never from code)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./paymentops.db"

    # Optional shared key for /api/* (webhook endpoint is exempt - PayPal can't send it).
    api_key: str | None = None
    enable_demo: bool = True
    frontend_origins: str = "http://localhost:3000"
    frontend_url: str = "http://localhost:3000"

    # PayPal (sandbox only)
    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None
    paypal_env: str = "sandbox"
    paypal_webhook_id: str | None = None
    paypal_timeout_s: float = 30.0
    allow_unverified_webhooks: bool = False  # dev only: stores events but never trusts them

    # LLM via OpenRouter
    openrouter_api_key: str | None = None
    openrouter_models: str = "openai/gpt-oss-20b:free,meta-llama/llama-3.3-70b-instruct:free"
    llm_timeout_s: float = 45.0

    # An initiated capture with no recorded outcome after this long counts as "unknown outcome".
    unresolved_after_seconds: int = 120

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def llm_models(self) -> list[str]:
        return [m.strip() for m in self.openrouter_models.split(",") if m.strip()]

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def secret_values(self) -> list[str]:
        vals = [self.paypal_client_secret, self.openrouter_api_key, self.api_key, self.database_url]
        return [v for v in vals if v and len(v) >= 8]


@lru_cache
def get_settings() -> Settings:
    return Settings()
