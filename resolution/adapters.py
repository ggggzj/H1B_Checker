"""
resolution.adapters — the prod adapters that satisfy the two ports.

This is the only file in ``resolution/`` that imports SQLAlchemy and OpenAI. It hides the
queries, the pgvector SQL, the ORM→value-object projection, and the embedding call + cache.
The core never sees a Session or an OpenAI client.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from openai import OpenAI
from sqlalchemy import text
from sqlalchemy.orm import Session

from models import Employer, EmployerAlias

from .types import EmployerRecord, ScoredEmployer

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


def _to_record(row: Optional[Employer]) -> Optional[EmployerRecord]:
    """Project an Employer ORM row into a pure EmployerRecord."""
    if row is None:
        return None
    return EmployerRecord(
        employer_name=row.employer_name,
        total_h1b_certified=row.total_h1b_certified or 0,
        earliest_decision_date=(
            str(row.earliest_decision_date) if row.earliest_decision_date else None
        ),
        latest_decision_date=(
            str(row.latest_decision_date) if row.latest_decision_date else None
        ),
        last_active_year=row.last_active_year,
        h1b_dependent=row.h1b_dependent,
    )


class SqlEmployerRepo:
    """Postgres adapter for the EmployerRepo port. Wraps a per-request Session."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def find_exact(self, normalized_name: str) -> Optional[EmployerRecord]:
        row = (
            self._db.query(Employer)
            .filter(Employer.employer_name == normalized_name)
            .first()
        )
        return _to_record(row)

    def find_by_alias(self, normalized_name: str) -> Optional[EmployerRecord]:
        alias = (
            self._db.query(EmployerAlias)
            .filter(EmployerAlias.alias_name == normalized_name)
            .first()
        )
        if not alias:
            return None
        primary = (
            self._db.query(Employer)
            .filter(Employer.employer_name == alias.primary_employer_name)
            .first()
        )
        return _to_record(primary)

    def find_fuzzy(self, normalized_name: str) -> Optional[EmployerRecord]:
        row = (
            self._db.query(Employer)
            .filter(Employer.employer_name.ilike(f"%{normalized_name}%"))
            .order_by(Employer.total_h1b_certified.desc())
            .first()
        )
        return _to_record(row)

    def vector_search(
        self, embedding: Sequence[float], k: int
    ) -> List[ScoredEmployer]:
        vec_lit = json.dumps([float(x) for x in embedding])
        sql = text(
            """
            SELECT
                employer_name,
                total_h1b_certified,
                earliest_decision_date,
                latest_decision_date,
                last_active_year,
                h1b_dependent,
                1 - (embedding <=> CAST(:emb AS vector)) AS similarity
            FROM employers
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT :k
            """
        )
        try:
            rows = self._db.execute(sql, {"emb": vec_lit, "k": k}).mappings().all()
        except Exception as e:  # pgvector / SQL error → degrade to no semantic match
            logger.warning("vector_search error err=%s", e, exc_info=True)
            return []

        return [
            ScoredEmployer(
                record=EmployerRecord(
                    employer_name=r["employer_name"],
                    total_h1b_certified=r["total_h1b_certified"] or 0,
                    earliest_decision_date=(
                        str(r["earliest_decision_date"])
                        if r["earliest_decision_date"]
                        else None
                    ),
                    latest_decision_date=(
                        str(r["latest_decision_date"])
                        if r["latest_decision_date"]
                        else None
                    ),
                    last_active_year=r["last_active_year"],
                    h1b_dependent=r["h1b_dependent"],
                ),
                similarity=float(r["similarity"]),
            )
            for r in rows
        ]


@lru_cache(maxsize=1000)
def _embedding_lru_cached(text_key: str) -> Tuple[float, ...]:
    """OpenAI embedding for text_key, cached by exact string. Raises on API error."""
    client = _shared_client()
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text_key)
    return tuple(float(x) for x in response.data[0].embedding)


_client: Optional[OpenAI] = None


def _shared_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


class OpenAIEmbedder:
    """OpenAI adapter for the Embedder port. Caching lives here, not in the core."""

    def embed(self, text_key: str) -> Optional[Sequence[float]]:
        try:
            return list(_embedding_lru_cached(text_key))
        except Exception as e:
            # Log only the exception type, never the message body: OpenAI's error
            # text can echo a fragment of the API key on auth failures, and logs
            # may be less protected than env vars. The type (AuthenticationError,
            # RateLimitError, APIConnectionError, ...) is enough to diagnose.
            logger.warning("embed error text=%r err_type=%s", text_key, type(e).__name__)
            return None
