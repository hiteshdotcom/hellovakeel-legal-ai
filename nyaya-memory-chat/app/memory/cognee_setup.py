"""Cognee configuration + memory-backend factory.

`configure_cognee()` sets Cognee's documented environment variables BEFORE cognee
is imported/used so it picks:
  * OpenAI for the internal extraction LLM,
  * text-embedding-3-large @ 1536 dims (matching judgment_vectors), and
  * Postgres for graph + vector + cache.

It asserts the embedding dimension is 1536 and that Cognee's target database is
NOT our judgment database (so Cognee can never write into the judgment tables).
"""
from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urlparse

from ..config import Settings, get_settings
from ..db import Database
from ..llm import LLMClients, get_llm
from .base import MemoryBackend

logger = logging.getLogger("nyaya.memory.cognee_setup")

_configured = False


def _judgment_db_identity(settings: Settings) -> tuple[str, str]:
    """(host, dbname) of OUR judgment database, for the collision check."""
    p = urlparse(settings.database_url)
    return (p.hostname or "", (p.path or "/").lstrip("/"))


def configure_cognee(settings: Optional[Settings] = None) -> None:
    """Idempotently push our config into Cognee's environment + runtime config."""
    global _configured
    if _configured:
        return
    s = settings or get_settings()

    # ---- assertions (fail fast, BEFORE touching cognee) ----
    assert s.EMBEDDING_DIMENSIONS == 1536, (
        f"EMBEDDING_DIMENSIONS must be 1536 to match judgment_vectors, got "
        f"{s.EMBEDDING_DIMENSIONS}."
    )
    j_host, j_db = _judgment_db_identity(s)
    if s.DB_HOST and s.DB_NAME:
        same_db = (s.DB_HOST == j_host) and (s.DB_NAME == j_db)
        assert not same_db, (
            "Cognee's target database must NOT be the judgment database "
            f"({j_host}/{j_db}). Set DB_NAME to a dedicated database (e.g. cognee_db)."
        )

    # ---- LLM (OpenAI for extraction) ----
    os.environ["LLM_API_KEY"] = s.OPENAI_API_KEY
    os.environ["LLM_PROVIDER"] = s.LLM_PROVIDER
    os.environ["LLM_MODEL"] = s.LLM_MODEL

    # ---- embeddings (MUST be 1536) ----
    os.environ["EMBEDDING_PROVIDER"] = "openai"
    os.environ["EMBEDDING_MODEL"] = s.EMBEDDING_MODEL
    os.environ["EMBEDDING_DIMENSIONS"] = str(s.EMBEDDING_DIMENSIONS)
    os.environ["EMBEDDING_API_KEY"] = s.OPENAI_API_KEY

    # ---- storage: whole memory layer on one Postgres ----
    os.environ["DB_PROVIDER"] = s.DB_PROVIDER
    os.environ["VECTOR_DB_PROVIDER"] = s.VECTOR_DB_PROVIDER
    os.environ["GRAPH_DATABASE_PROVIDER"] = s.GRAPH_DATABASE_PROVIDER
    os.environ["CACHE_BACKEND"] = s.CACHE_BACKEND
    os.environ["DB_HOST"] = s.DB_HOST
    os.environ["DB_PORT"] = str(s.DB_PORT)
    os.environ["DB_USERNAME"] = s.DB_USERNAME
    os.environ["DB_PASSWORD"] = s.DB_PASSWORD
    os.environ["DB_NAME"] = s.DB_NAME

    # ---- optional Neo4j (demo visual only) ----
    if s.GRAPH_DATABASE_PROVIDER == "neo4j" and s.GRAPH_DATABASE_URL:
        os.environ["GRAPH_DATABASE_URL"] = s.GRAPH_DATABASE_URL
        os.environ["GRAPH_DATABASE_USERNAME"] = s.GRAPH_DATABASE_USERNAME
        os.environ["GRAPH_DATABASE_PASSWORD"] = s.GRAPH_DATABASE_PASSWORD

    # Best-effort: also push into cognee's runtime config object if available.
    try:  # pragma: no cover - only when cognee installed
        import cognee

        for setter, val in [
            ("set_llm_provider", s.LLM_PROVIDER),
            ("set_llm_model", s.LLM_MODEL),
            ("set_llm_api_key", s.OPENAI_API_KEY),
        ]:
            fn = getattr(cognee.config, setter, None)
            if callable(fn):
                fn(val)
        logger.info("Configured cognee v%s", getattr(cognee, "__version__", "?"))
    except Exception as exc:  # noqa: BLE001
        logger.info("cognee runtime config not applied (%s) — env vars are set.", exc)

    _configured = True


def _cognee_importable() -> bool:
    try:
        import cognee  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def get_memory_backend(
    db: Database, llm: Optional[LLMClients] = None, settings: Optional[Settings] = None
) -> MemoryBackend:
    """Choose the memory backend per MEMORY_BACKEND (cognee | local | auto)."""
    s = settings or get_settings()
    llm = llm or get_llm()
    choice = (s.MEMORY_BACKEND or "auto").lower()

    if choice in ("cognee", "auto") and _cognee_importable():
        try:
            configure_cognee(s)
            from .cognee_backend import CogneeBackend

            logger.info("Using Cognee memory backend.")
            return CogneeBackend(db, llm, s)
        except Exception as exc:  # noqa: BLE001
            if choice == "cognee":
                raise
            logger.warning("Cognee backend init failed (%s) — falling back to local.", exc)

    if choice == "cognee":
        raise RuntimeError(
            "MEMORY_BACKEND=cognee but cognee is not importable. "
            'Install it with: pip install "cognee[postgres]"'
        )

    from .local_backend import LocalBackend

    logger.info("Using local (Postgres) memory backend.")
    return LocalBackend(db, llm)
