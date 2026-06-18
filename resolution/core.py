"""
resolution.core — the deep, pure resolution module.

``EmployerResolver.resolve`` reproduces the four-layer match order (exact → alias → fuzzy
→ semantic) plus the semantic rerank / threshold / ambiguity rules. It depends only on the
two ports, so the whole algorithm — including the subtle semantic logic most likely to
regress — is unit-testable with in-memory fakes: no DB, no network.
"""

from __future__ import annotations

import math
from typing import List

from clean_data import normalize_employer

from .ports import Embedder, EmployerRepo
from .types import Resolution, ScoredEmployer

# Fetch more neighbours than we keep, so rerank has runners-up to weigh.
_SEMANTIC_FETCH_LIMIT = 10
# When top-1 and top-2 similarities are within this gap, break the near-tie using
# certified-filing volume (similarity * log1p(total_h1b_certified)).
_SIM_TIGHT_SCORE_GAP = 0.04
# A semantic hit "almost tied" with the runner-up (margin below this) is too noisy: reject.
_AMBIGUITY_REJECT_MARGIN = 0.04
# A margin below this marks the hit ambiguous (confidence gets damped).
_AMBIGUITY_MARGIN = 0.1
# Confidence multiplier applied to ambiguous semantic hits.
_AMBIGUOUS_DAMPING = 0.9


class EmployerResolver:
    """Resolve a raw company string to one best :class:`EmployerRecord`."""

    def __init__(
        self,
        repo: EmployerRepo,
        embedder: Embedder,
        *,
        similarity_threshold: float = 0.75,
        fetch_k: int = _SEMANTIC_FETCH_LIMIT,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._threshold = similarity_threshold
        self._fetch_k = fetch_k

    def resolve(self, raw_name: str) -> Resolution:
        """The interface: raw name in, one :class:`Resolution` out.

        Layers are tried in order and the first hit wins. Layers 1–3 never embed, so a
        semantic-only outage (no key / OpenAI down) can't slow an exact match.
        """
        name = normalize_employer(raw_name)
        if not name:
            return Resolution(None, "invalid_input", None)

        record = self._repo.find_exact(name)
        if record:
            return Resolution(record, "exact", 1.0)

        record = self._repo.find_by_alias(name)
        if record:
            return Resolution(record, "alias", 1.0)

        record = self._repo.find_fuzzy(name)
        if record:
            return Resolution(record, "fuzzy", 1.0)

        return self._semantic(name)

    # ---- semantic layer (the deep core) ----

    def _semantic(self, name: str) -> Resolution:
        embedding = self._embedder.embed(name)
        if not embedding:
            return Resolution(None, "miss", None)

        candidates = self._repo.vector_search(embedding, self._fetch_k)
        if not candidates:
            return Resolution(None, "miss", None)

        candidates = self._rerank(candidates)
        top1 = candidates[0].similarity

        margin = None
        is_ambiguous = False
        if len(candidates) > 1:
            margin = top1 - candidates[1].similarity
            if margin < _AMBIGUITY_MARGIN:
                is_ambiguous = True

        if top1 < self._threshold:
            return Resolution(None, "miss", None)

        if is_ambiguous and margin is not None and margin < _AMBIGUITY_REJECT_MARGIN:
            return Resolution(None, "miss", None)

        confidence = top1 * _AMBIGUOUS_DAMPING if is_ambiguous else top1
        return Resolution(candidates[0].record, "semantic", confidence)

    def _rerank(self, candidates: List[ScoredEmployer]) -> List[ScoredEmployer]:
        """When the top hits bunch up, prefer the employer with more certified filings
        (a real partnership over a near-identically-named shell), but never promote one
        below the threshold floor or reorder when the top hit already wins."""
        if len(candidates) < 2:
            return candidates

        s0 = candidates[0].similarity
        s1 = candidates[1].similarity
        if s0 - s1 >= _SIM_TIGHT_SCORE_GAP:
            return candidates

        floor = max(self._threshold - 0.05, min(s0, s1) - 0.02)
        pool = [c for c in candidates[:6] if c.similarity >= floor]
        if len(pool) < 2:
            return candidates

        best = max(
            pool,
            key=lambda c: c.similarity
            * math.log1p(max(int(c.record.total_h1b_certified or 0), 0)),
        )
        if best.similarity < self._threshold:
            return candidates
        if best is candidates[0]:
            return candidates
        return [best] + [c for c in candidates if c is not best]
