"""Employer resolution: raw company string → one best Employer.

The deep core (``EmployerResolver``) is pure and depends only on the two ports
(``EmployerRepo``, ``Embedder``). Prod wires the Postgres / OpenAI adapters; tests wire
in-memory fakes. See CONTEXT.md for the vocabulary.
"""

from .adapters import OpenAIEmbedder, SqlEmployerRepo
from .core import EmployerResolver
from .ports import Embedder, EmployerRepo
from .types import EmployerRecord, MatchType, Resolution, ScoredEmployer

__all__ = [
    "EmployerResolver",
    "Resolution",
    "EmployerRecord",
    "ScoredEmployer",
    "MatchType",
    "EmployerRepo",
    "Embedder",
    "SqlEmployerRepo",
    "OpenAIEmbedder",
]
