"""
Tests for /config — the remote rules the extension hot-loads.

Covers the new `no_sponsor` block (per-posting 🔴 detection) end to end:
  1. The /config HTTP route ships version + selectors + no_sponsor.
  2. The shipped negative/affirmative patterns are valid regex and encode the
     intended conservative behavior (flag only when a negative matches AND no
     affirmative does). This mirrors the JS detector in content.js — the Python
     side is the source of truth that gets delivered, so it's worth pinning here.
"""

import re

from fastapi.testclient import TestClient

import main
from main import (
    app,
    EXTENSION_CONFIG_VERSION,
    EXTENSION_NO_SPONSOR_NEGATIVE,
    EXTENSION_NO_SPONSOR_AFFIRMATIVE,
)


# ---------- /config route ----------

def test_config_ships_version_and_no_sponsor():
    client = TestClient(app)
    body = client.get("/config").json()
    assert body["version"] == EXTENSION_CONFIG_VERSION
    assert "selectors" in body
    assert "company_name" in body["selectors"]
    assert "job_card" in body["selectors"]
    ns = body["no_sponsor"]
    assert ns["negative"] == EXTENSION_NO_SPONSOR_NEGATIVE
    assert ns["affirmative"] == EXTENSION_NO_SPONSOR_AFFIRMATIVE
    assert len(ns["negative"]) > 0


def test_all_patterns_are_valid_regex():
    # A malformed pattern would silently break detection on every client.
    for src in EXTENSION_NO_SPONSOR_NEGATIVE + EXTENSION_NO_SPONSOR_AFFIRMATIVE:
        re.compile(src, re.IGNORECASE)


# ---------- behavior of the shipped rules (mirror of the JS detector) ----------

def _detect(text):
    """Conservative: flag only if a negative matches AND no affirmative does."""
    neg = [re.compile(s, re.IGNORECASE) for s in EXTENSION_NO_SPONSOR_NEGATIVE]
    aff = [re.compile(s, re.IGNORECASE) for s in EXTENSION_NO_SPONSOR_AFFIRMATIVE]
    if not any(r.search(text) for r in neg):
        return False
    return not any(r.search(text) for r in aff)


FLAG_CASES = [
    "We are unable to sponsor visas for this position.",
    "This role does not offer visa sponsorship.",
    "Must be a US citizen. Citizenship is required.",
    "U.S. Citizenship is required for this role.",
    "Candidates must be authorized to work without sponsorship.",
    "Citizens only.",
    "Not eligible for sponsorship.",
    "Cannot provide visa sponsorship at this time.",
]

NO_FLAG_CASES = [
    "We will sponsor qualified candidates.",
    "Visa sponsorship available for the right candidate.",
    "We do sponsor H1B for exceptional engineers.",
    "We are able to sponsor work visas.",
    "Great team, competitive salary, remote friendly.",
    # Both a denial and an affirmation present -> conservative: don't flag.
    "We do not sponsor for this role, but we will sponsor for senior roles.",
    # Known conservative miss: affirmative "sponsorship ... available" suppresses it.
    "No sponsorship is available for this position.",
]


def test_negative_jds_are_flagged():
    for text in FLAG_CASES:
        assert _detect(text) is True, text


def test_affirmative_or_neutral_jds_are_not_flagged():
    for text in NO_FLAG_CASES:
        assert _detect(text) is False, text
