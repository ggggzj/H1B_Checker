"""
End-to-end tests for the `tier` field on the /check response.

Two layers, no DB / no OpenAI:
  1. to_check_response() — the pure mapper from Resolution -> CheckResponse, on both
     the miss branch and the hit branch, plus JSON serialization of `tier`.
  2. The real /check HTTP route through FastAPI's TestClient, with the resolver and the
     API-key dependency overridden so the request never leaves the process. Confirms
     `tier` survives all the way into the actual HTTP JSON body.
"""

import pytest
from fastapi.testclient import TestClient

import main
from main import app, get_resolver, require_api_key, to_check_response
from resolution import EmployerRecord, Resolution


# ---------- helpers ----------

def _hit(match_type="exact", confidence=1.0, **record_kwargs):
    defaults = dict(
        employer_name="ACME CORP",
        total_h1b_certified=50,
        last_active_year=2025,
        earliest_decision_date="2015-01-01",
        latest_decision_date="2025-01-01",
    )
    defaults.update(record_kwargs)
    return Resolution(
        record=EmployerRecord(**defaults),
        match_type=match_type,
        confidence=confidence,
    )


def _miss(match_type="miss", confidence=None):
    return Resolution(record=None, match_type=match_type, confidence=confidence)


# ---------- to_check_response: miss branch ----------

def test_miss_response_tier_is_none():
    resp = to_check_response(_miss())
    assert resp.found is False
    assert resp.sponsors_h1b is False
    assert resp.tier == "none"


def test_invalid_input_response_tier_is_none():
    resp = to_check_response(_miss(match_type="invalid_input"))
    assert resp.found is False
    assert resp.match_type == "invalid_input"
    assert resp.tier == "none"


# ---------- to_check_response: hit branch ----------

def test_hit_response_strong_tier():
    resp = to_check_response(_hit())
    assert resp.found is True
    assert resp.sponsors_h1b is True
    assert resp.employer_name == "ACME CORP"
    assert resp.h1b_count == 50
    assert resp.tier == "strong"


def test_hit_response_weak_when_old():
    resp = to_check_response(_hit(last_active_year=2018))
    assert resp.found is True
    assert resp.tier == "weak"


def test_hit_response_weak_when_low_confidence_semantic():
    resp = to_check_response(_hit(match_type="semantic", confidence=0.80))
    assert resp.tier == "weak"


def test_hit_response_strong_when_high_confidence_semantic():
    resp = to_check_response(_hit(match_type="semantic", confidence=0.90))
    assert resp.tier == "strong"


# ---------- serialization: tier survives into JSON ----------

def test_tier_serializes_to_json_on_hit():
    payload = to_check_response(_hit()).model_dump()
    assert payload["tier"] == "strong"
    # The badge logic upstream depends on these riding together.
    assert payload["found"] is True
    assert payload["sponsors_h1b"] is True


def test_tier_serializes_to_json_on_miss():
    payload = to_check_response(_miss()).model_dump()
    assert payload["tier"] == "none"
    assert payload["found"] is False


def test_tier_key_always_present_in_schema():
    # Even though tier is Optional, the mapper always populates it, so the key is never
    # absent — the extension can read data.tier without an existence guard.
    assert "tier" in to_check_response(_hit()).model_dump()
    assert "tier" in to_check_response(_miss()).model_dump()


# ---------- API-level: real /check route, mocked resolver ----------

class _StubResolver:
    """Returns whatever Resolution it was constructed with — no DB, no OpenAI."""

    def __init__(self, resolution: Resolution):
        self._resolution = resolution

    def resolve(self, name: str) -> Resolution:
        return self._resolution


@pytest.fixture
def client_for():
    """Yield a factory that builds a TestClient wired to a fixed Resolution."""
    def _make(resolution: Resolution) -> TestClient:
        app.dependency_overrides[get_resolver] = lambda: _StubResolver(resolution)
        app.dependency_overrides[require_api_key] = lambda: None
        main.limiter.reset()
        main.limiter.enabled = False  # don't let the per-IP cap interfere
        return TestClient(app)

    try:
        yield _make
    finally:
        main.limiter.reset()
        main.limiter.enabled = True
        app.dependency_overrides.clear()


def test_check_http_returns_strong_tier(client_for):
    client = client_for(_hit())
    body = client.get("/check", params={"company": "acme"}).json()
    assert body["tier"] == "strong"
    assert body["found"] is True
    assert body["sponsors_h1b"] is True


def test_check_http_returns_weak_tier(client_for):
    client = client_for(_hit(match_type="semantic", confidence=0.80))
    body = client.get("/check", params={"company": "acme-ish"}).json()
    assert body["tier"] == "weak"
    assert body["found"] is True


def test_check_http_returns_none_tier_on_miss(client_for):
    client = client_for(_miss())
    resp = client.get("/check", params={"company": "nope12345"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "none"
    assert body["found"] is False
    assert body["sponsors_h1b"] is False
