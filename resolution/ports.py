"""
resolution.ports — the seams the resolution core depends on.

Two ports, each with a prod adapter (Postgres / OpenAI) and an in-memory fake for tests.
The core imports only these Protocols, never SQLAlchemy or openai.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, Sequence

from .types import EmployerRecord, ScoredEmployer


class EmployerRepo(Protocol):
    """Seam over the employer table. Each method is one query; the alias two-hop and the
    pgvector SQL are hidden behind it."""

    def find_exact(self, normalized_name: str) -> Optional[EmployerRecord]:
        """Employer whose name equals ``normalized_name`` exactly, else None."""

    def find_by_alias(self, normalized_name: str) -> Optional[EmployerRecord]:
        """Primary employer for a known alias/DBA name, else None (two-hop hidden)."""

    def find_fuzzy(self, normalized_name: str) -> Optional[EmployerRecord]:
        """Best ILIKE ``%name%`` match (highest total_h1b_certified), else None."""

    def vector_search(self, embedding: Sequence[float], k: int) -> List[ScoredEmployer]:
        """Top-k nearest employers by cosine similarity, sorted similarity-descending.

        Similarity is computed DB-side. Returns [] on any backend error — the core treats
        an empty list as 'no semantic match', never a 500."""


class Embedder(Protocol):
    """Seam over the embedding provider."""

    def embed(self, text: str) -> Optional[Sequence[float]]:
        """Embedding vector for ``text``, or None on failure (no key, API error).

        Never raises across the seam — failure is data-shaped so the semantic layer can
        degrade to a clean miss."""
