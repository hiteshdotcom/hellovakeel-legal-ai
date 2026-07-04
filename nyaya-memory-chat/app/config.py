"""Application configuration.

Reads the SAME environment the existing Nyaya.AI / lex-ai backend uses (the
project root `.env`), plus Cognee's documented variable names. Nothing here is
specific to a single deployment — every value comes from the environment.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The shared .env lives one level up (the existing monolith's env file). We also
# accept a local .env inside the service directory.
_ROOT = Path(__file__).resolve().parents[1]
_ENV_CANDIDATES = [
    _ROOT / ".env",
    _ROOT.parent / ".env",  # the shared lex-ai/.env the user pasted
]


def _strip_self_prefix(value: str, key: str) -> str:
    """The pasted .env has `DATABASE_URL="DATABASE_URL=postgres://..."` — a
    double-prefix typo. Strip a leading `KEY=` if the value accidentally carries
    its own name."""
    if not value:
        return value
    prefix = f"{key}="
    return value[len(prefix):] if value.startswith(prefix) else value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[str(p) for p in _ENV_CANDIDATES if p.exists()],
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── OUR app: Supabase / judgments (read-only source of truth) ──
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_ANON_KEY: str = ""
    DATABASE_URL: str = ""

    # ── OUR app: AI providers ──
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    COHERE_API_KEY: str = ""
    CLAUDE_MODEL_REASONING: str = "claude-opus-4-8"
    CLAUDE_MODEL_FAST: str = "claude-sonnet-4-6"

    # ── Embeddings (MUST be 1536 to match judgment_vectors) ──
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    EMBEDDING_DIMENSIONS: int = Field(default=1536, alias="EMBEDDING_DIMS")

    # ── Retrieval knobs ──
    HYBRID_SEARCH_LIMIT: int = 20
    RERANK_TOP_N: int = 10

    # ── COGNEE: LLM for graph extraction (use OpenAI) ──
    LLM_PROVIDER: str = "openai"
    LLM_MODEL: str = "gpt-4o-mini"

    # ── COGNEE: storage ──
    DB_PROVIDER: str = "postgres"
    VECTOR_DB_PROVIDER: str = "pgvector"
    GRAPH_DATABASE_PROVIDER: str = "postgres"
    CACHE_BACKEND: str = "postgres"
    DB_HOST: str = ""
    DB_PORT: int = 5432
    DB_USERNAME: str = ""
    DB_PASSWORD: str = ""
    DB_NAME: str = "cognee_db"

    # ── COGNEE: optional Neo4j (demo visual only) ──
    GRAPH_DATABASE_URL: str = ""
    GRAPH_DATABASE_USERNAME: str = ""
    GRAPH_DATABASE_PASSWORD: str = ""

    # ── Memory backend selection ──
    # "cognee"  -> use the real Cognee high/low level API (requires `pip install "cognee[postgres]"`)
    # "local"   -> Postgres-backed fallback memory in the `memchat` schema (default for demo/tests)
    # "auto"    -> try cognee, fall back to local if import/config fails
    MEMORY_BACKEND: str = "auto"

    # ── Auth: sessions + Google OAuth ──
    # Email/password works with no extra config. Google sign-in activates as soon
    # as GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET are present. OAUTH_REDIRECT_BASE
    # overrides the auto-detected public origin used to build the redirect URI
    # (e.g. "https://app.example.com"); leave blank to derive it from the request.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    OAUTH_REDIRECT_BASE: str = ""
    AUTH_SESSION_DAYS: int = 30

    # ── Auth: Clerk (hosted auth; powers "Sign in with Google") ──
    # The publishable key is public (shipped to the browser); the secret key is
    # used server-side to read the authoritative user profile after we verify
    # the Clerk session token against Clerk's JWKS.
    CLERK_PUBLISHABLE_KEY: str = ""
    CLERK_SECRET_KEY: str = ""
    CLERK_FRONTEND_API: str = ""  # optional override; else decoded from the publishable key
    CLERK_API_BASE: str = "https://api.clerk.com/v1"

    # ── Service ──
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    ENVIRONMENT: str = "production"
    DEBUG: bool = False

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    @property
    def clerk_enabled(self) -> bool:
        return bool(self.CLERK_PUBLISHABLE_KEY and self.CLERK_SECRET_KEY)

    @property
    def clerk_frontend_api(self) -> str:
        """The Clerk Frontend API host, e.g. `clever-orca-83.clerk.accounts.dev`.
        Decoded from the publishable key (`pk_test_<base64(host$)>`) unless set."""
        if self.CLERK_FRONTEND_API:
            return self.CLERK_FRONTEND_API
        pk = self.CLERK_PUBLISHABLE_KEY
        if not pk:
            return ""
        try:
            import base64

            b = pk.split("_", 2)[-1]
            b += "=" * (-len(b) % 4)
            return base64.urlsafe_b64decode(b).decode("utf-8").rstrip("$").strip("/")
        except Exception:  # noqa: BLE001
            return ""

    @property
    def clerk_issuer(self) -> str:
        fa = self.clerk_frontend_api
        return f"https://{fa}" if fa else ""

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _fix_database_url(cls, v: object) -> object:
        if isinstance(v, str):
            return _strip_self_prefix(v.strip(), "DATABASE_URL")
        return v

    # ----- derived helpers -----
    @property
    def database_url(self) -> str:
        """asyncpg-friendly DSN (postgresql://...). asyncpg also accepts
        `postgres://`. We normalise the scheme just in case."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url

    @property
    def cognee_db_url(self) -> Optional[str]:
        """Build the Cognee target Postgres DSN from DB_* parts if present."""
        if self.DB_HOST and self.DB_USERNAME:
            return (
                f"postgresql://{self.DB_USERNAME}:{self.DB_PASSWORD}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        return None

    @property
    def use_cohere(self) -> bool:
        return bool(self.COHERE_API_KEY)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Mirror our keys into the names Cognee reads from the raw environment.
    # configure_cognee() does the authoritative set, but doing it here too keeps
    # any early `import cognee` consistent.
    os.environ.setdefault("LLM_API_KEY", s.OPENAI_API_KEY)
    os.environ.setdefault("EMBEDDING_API_KEY", s.OPENAI_API_KEY)
    return s
