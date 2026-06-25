"""
Unit tests for classify_tier — the deterministic 3-tier badge logic.

classify_tier is a pure function of a Resolution (no DB, no network), so these run
with plain value objects and no fakes. Defaults under test: recent year 2024,
strong min count 5, strong semantic confidence 0.85 (see main.py).
"""

from resolution import EmployerRecord, Resolution
import main
from main import classify_tier


def _res(match_type="exact", confidence=1.0, **record_kwargs):
    """Build a Resolution with a record, defaulting to a strong-looking employer."""
    defaults = dict(
        employer_name="ACME CORP",
        total_h1b_certified=50,
        last_active_year=2025,
    )
    defaults.update(record_kwargs)
    return Resolution(
        record=EmployerRecord(**defaults),
        match_type=match_type,
        confidence=confidence,
    )


def test_miss_is_none():
    res = Resolution(record=None, match_type="miss", confidence=None)
    assert classify_tier(res) == "none"


def test_recent_sizable_trusted_is_strong():
    assert classify_tier(_res()) == "strong"


def test_old_is_weak():
    # Last active before the recent-year cutoff -> not strong.
    assert classify_tier(_res(last_active_year=2019)) == "weak"


def test_too_small_is_weak():
    assert classify_tier(_res(total_h1b_certified=2)) == "weak"


def test_count_at_threshold_is_strong():
    # 5 is the boundary and counts as sizable (>=).
    assert classify_tier(_res(total_h1b_certified=5)) == "strong"


def test_year_at_threshold_is_strong():
    assert classify_tier(_res(last_active_year=2024)) == "strong"


def test_low_confidence_semantic_is_weak():
    assert classify_tier(_res(match_type="semantic", confidence=0.80)) == "weak"


def test_high_confidence_semantic_is_strong():
    assert classify_tier(_res(match_type="semantic", confidence=0.90)) == "strong"


def test_fuzzy_is_trusted_regardless_of_confidence():
    # Name-anchored matches (exact/alias/fuzzy) are trusted even with low confidence.
    assert classify_tier(_res(match_type="fuzzy", confidence=0.1)) == "strong"


def test_missing_fields_default_to_weak():
    # None count / None year coerce to 0 -> not sizable, not recent.
    assert classify_tier(_res(total_h1b_certified=0, last_active_year=None)) == "weak"


# ---- additional edge cases: each match_type, boundary confidence, env tunability ----

def test_exact_is_trusted():
    assert classify_tier(_res(match_type="exact")) == "strong"


def test_alias_is_trusted():
    assert classify_tier(_res(match_type="alias")) == "strong"


def test_exact_with_none_confidence_is_trusted():
    # Name-anchored matches don't depend on confidence at all.
    assert classify_tier(_res(match_type="exact", confidence=None)) == "strong"


def test_semantic_at_threshold_is_strong():
    # 0.85 is the boundary and counts as trusted (>=).
    assert classify_tier(_res(match_type="semantic", confidence=0.85)) == "strong"


def test_semantic_just_below_threshold_is_weak():
    assert classify_tier(_res(match_type="semantic", confidence=0.8499)) == "weak"


def test_semantic_with_none_confidence_is_weak():
    # (res.confidence or 0) coerces None -> 0 -> below the semantic bar -> not trusted.
    assert classify_tier(_res(match_type="semantic", confidence=None)) == "weak"


def test_trusted_but_old_and_small_is_weak():
    # Trusted alone is not enough: still needs recent AND sizable.
    assert classify_tier(
        _res(match_type="exact", total_h1b_certified=1, last_active_year=2000)
    ) == "weak"


def test_future_year_is_recent():
    assert classify_tier(_res(last_active_year=2099)) == "strong"


def test_thresholds_are_env_tunable(monkeypatch):
    # classify_tier reads the module globals at call time, so raising the recent-year
    # bar above a record's last_active_year flips a previously-strong hit to weak.
    monkeypatch.setattr(main, "TIER_RECENT_YEAR", 2030)
    assert classify_tier(_res(last_active_year=2025)) == "weak"
    monkeypatch.setattr(main, "TIER_RECENT_YEAR", 2024)
    assert classify_tier(_res(last_active_year=2025)) == "strong"


def test_strong_min_count_is_env_tunable(monkeypatch):
    monkeypatch.setattr(main, "TIER_STRONG_MIN_COUNT", 1000)
    assert classify_tier(_res(total_h1b_certified=50)) == "weak"
