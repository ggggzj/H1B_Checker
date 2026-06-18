"""
Pure-core tests for the resolution module.

These exercise the four-layer ordering and the whole semantic algorithm (rerank,
threshold, ambiguity, damping) with in-memory fakes — no Postgres, no OpenAI. This is the
payoff of the repo + embedder seams: the logic most likely to regress is unit-testable.
"""

import math

import pytest

from resolution import EmployerRecord, EmployerResolver, ScoredEmployer


# ---- in-memory adapters (the second adapter for each seam) ----

class FakeRepo:
    def __init__(self, *, exact=None, aliases=None, fuzzy=None, neighbors=None):
        self._exact = exact or {}        # normalized name -> EmployerRecord
        self._aliases = aliases or {}    # normalized alias -> primary EmployerRecord
        self._fuzzy = fuzzy or {}        # normalized name -> EmployerRecord
        self._neighbors = list(neighbors or [])  # pre-sorted [ScoredEmployer]

    def find_exact(self, name):
        return self._exact.get(name)

    def find_by_alias(self, name):
        return self._aliases.get(name)

    def find_fuzzy(self, name):
        return self._fuzzy.get(name)

    def vector_search(self, embedding, k):
        return list(self._neighbors[:k])


class FakeEmbedder:
    def __init__(self, table=None):
        self._t = table or {}

    def embed(self, text):
        return self._t.get(text)


def rec(name, n=0):
    return EmployerRecord(employer_name=name, total_h1b_certified=n)


def resolver(repo, *, embedder=None, threshold=0.75):
    return EmployerResolver(
        repo,
        embedder or FakeEmbedder(),
        similarity_threshold=threshold,
    )


# ---- layer ordering / short-circuit ----

def test_invalid_input_when_name_normalizes_to_empty():
    res = resolver(FakeRepo()).resolve("   ")
    assert res.match_type == "invalid_input"
    assert res.record is None and res.confidence is None


def test_exact_match_normalizes_input():
    goog = rec("GOOGLE LLC", 5000)
    res = resolver(FakeRepo(exact={"GOOGLE LLC": goog})).resolve("  google   llc ")
    assert res.match_type == "exact"
    assert res.confidence == 1.0
    assert res.record is goog


def test_exact_beats_alias_and_fuzzy():
    target = rec("ACME INC", 10)
    repo = FakeRepo(
        exact={"ACME INC": target},
        aliases={"ACME INC": rec("WRONG ALIAS", 99)},
        fuzzy={"ACME INC": rec("WRONG FUZZY", 99)},
    )
    res = resolver(repo).resolve("acme inc")
    assert res.match_type == "exact" and res.record is target


def test_alias_resolves_to_primary_when_exact_misses():
    primary = rec("GOOGLE LLC", 5000)
    repo = FakeRepo(aliases={"GOOGL": primary})
    res = resolver(repo).resolve("googl")
    assert res.match_type == "alias"
    assert res.confidence == 1.0
    assert res.record is primary


def test_fuzzy_fires_when_exact_and_alias_miss():
    hit = rec("GOOGLE LLC", 5000)
    res = resolver(FakeRepo(fuzzy={"GOOGLE": hit})).resolve("google")
    assert res.match_type == "fuzzy" and res.record is hit


# ---- semantic layer: threshold ----

def test_semantic_hit_above_threshold():
    meta = rec("META PLATFORMS INC", 8000)
    repo = FakeRepo(neighbors=[ScoredEmployer(meta, 0.88)])
    res = resolver(repo, embedder=FakeEmbedder({"FACEBOOK": [0.1]})).resolve("facebook")
    assert res.match_type == "semantic"
    assert res.record is meta
    assert res.confidence == pytest.approx(0.88)


def test_semantic_below_threshold_is_miss():
    repo = FakeRepo(neighbors=[ScoredEmployer(rec("SOMECO", 3), 0.70)])
    res = resolver(repo, embedder=FakeEmbedder({"XYZ": [0.1]}), threshold=0.75).resolve("xyz")
    assert res.match_type == "miss"
    assert res.record is None


def test_embedder_failure_degrades_to_miss():
    repo = FakeRepo(neighbors=[ScoredEmployer(rec("SOMECO", 3), 0.99)])
    # FakeEmbedder has no entry for "XYZ" -> embed returns None
    res = resolver(repo, embedder=FakeEmbedder({})).resolve("xyz")
    assert res.match_type == "miss"


def test_no_neighbors_is_miss():
    res = resolver(FakeRepo(neighbors=[]), embedder=FakeEmbedder({"XYZ": [0.1]})).resolve("xyz")
    assert res.match_type == "miss"


# ---- semantic layer: ambiguity ----

def test_ambiguous_tight_margin_rejected():
    # margin 0.02 < 0.04 reject-margin -> miss
    repo = FakeRepo(neighbors=[
        ScoredEmployer(rec("ACME CORP", 500), 0.81),
        ScoredEmployer(rec("ACME CO", 12), 0.79),
    ])
    res = resolver(repo, embedder=FakeEmbedder({"ACME": [0.1]})).resolve("acme")
    assert res.match_type == "miss"


def test_ambiguous_wide_margin_hits_with_damped_confidence():
    # margin 0.06: ambiguous (< 0.1) but not rejected (>= 0.04) -> hit, confidence * 0.9
    top = rec("STRIPE INC", 900)
    repo = FakeRepo(neighbors=[
        ScoredEmployer(top, 0.86),
        ScoredEmployer(rec("STRIPED CO", 5), 0.80),
    ])
    res = resolver(repo, embedder=FakeEmbedder({"STRIPE": [0.1]})).resolve("stripe")
    assert res.match_type == "semantic"
    assert res.record is top
    assert res.confidence == pytest.approx(0.86 * 0.9)


def test_unambiguous_wide_margin_full_confidence():
    top = rec("NVIDIA CORP", 4000)
    repo = FakeRepo(neighbors=[
        ScoredEmployer(top, 0.95),
        ScoredEmployer(rec("NVID CO", 2), 0.60),
    ])
    res = resolver(repo, embedder=FakeEmbedder({"NVIDIA": [0.1]})).resolve("nvidia")
    assert res.match_type == "semantic"
    assert res.confidence == pytest.approx(0.95)


# ---- semantic layer: rerank by filing volume ----

def test_rerank_promotion_is_currently_rejected_as_ambiguous():
    """CHARACTERIZATION of the existing behaviour, faithfully carried over from main.py.

    Rerank promotes the higher-volume runner-up (big) above the top similarity hit (small).
    After the reorder, top1=big(0.80) and top2=small(0.82), so margin=-0.02 < 0.04 and the
    ambiguous-tight-margin check rejects to a miss. Net: when the volume tiebreak actually
    fires, today's code never returns the promoted employer. This pins the current behaviour
    so fixing the quirk is a deliberate change, not an accident.
    """
    big = rec("BIG PARTNERS LLP", 90000)
    small = rec("BIG SHELL LLC", 3)
    repo = FakeRepo(neighbors=[
        ScoredEmployer(small, 0.82),
        ScoredEmployer(big, 0.80),
    ])
    res = resolver(repo, embedder=FakeEmbedder({"BIG": [0.1]})).resolve("big")
    assert res.match_type == "miss"


def test_rerank_keeps_order_when_gap_is_wide():
    top = rec("SMALL BUT EXACT", 1)
    other = rec("HUGE VOLUME CO", 100000)
    repo = FakeRepo(neighbors=[
        ScoredEmployer(top, 0.95),   # gap 0.10 >= 0.04 -> no rerank
        ScoredEmployer(other, 0.85),
    ])
    res = resolver(repo, embedder=FakeEmbedder({"Q": [0.1]})).resolve("q")
    assert res.record is top


def test_rerank_never_promotes_below_threshold():
    # near-tie, but both below threshold -> no promotion, top1 still < threshold -> miss
    a = rec("A CO", 5)
    b = rec("B PARTNERS", 90000)
    repo = FakeRepo(neighbors=[
        ScoredEmployer(a, 0.72),
        ScoredEmployer(b, 0.71),
    ])
    res = resolver(repo, embedder=FakeEmbedder({"Z": [0.1]}), threshold=0.75).resolve("z")
    assert res.match_type == "miss"


def test_rerank_volume_formula_matches_spec():
    # sanity-check the tiebreak metric the core uses
    assert 0.80 * math.log1p(90000) > 0.82 * math.log1p(3)
