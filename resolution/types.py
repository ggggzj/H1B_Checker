"""
resolution.types — pure value objects for employer resolution.

These cross the repo seam and come out of the resolution module. They carry only plain
data: no SQLAlchemy, no OpenAI, no live Session. The repo adapter projects ORM rows into
``EmployerRecord``; the API layer maps ``Resolution`` into its HTTP response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# How a resolution was reached. "invalid_input" = empty after normalization;
# "miss" = no layer matched.
MatchType = Literal["exact", "alias", "fuzzy", "semantic", "invalid_input", "miss"]


@dataclass(frozen=True)
class EmployerRecord:
    """Flat projection of one employer row — exactly the fields a response needs."""

    employer_name: str
    total_h1b_certified: int  # never None; the adapter coerces None -> 0
    earliest_decision_date: Optional[str] = None
    latest_decision_date: Optional[str] = None
    last_active_year: Optional[int] = None
    h1b_dependent: Optional[bool] = None


@dataclass(frozen=True)
class ScoredEmployer:
    """One semantic neighbour: a record plus its cosine similarity (1 - distance).

    Similarity is computed DB-side by pgvector, so it rides along with the record across
    the repo seam. The resolution core ranks and thresholds these; the adapter does not.
    """

    record: EmployerRecord
    similarity: float


@dataclass(frozen=True)
class Resolution:
    """The resolution module's answer."""

    record: Optional[EmployerRecord]
    match_type: MatchType
    confidence: Optional[float]

    @property
    def found(self) -> bool:
        return self.record is not None
