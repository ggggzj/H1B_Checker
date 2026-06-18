"""
Rate-limiting tests for the denial-of-wallet protection on /check.

These hit the real FastAPI app through TestClient, but stub out the resolver so no request
ever touches the database or OpenAI — we're testing the per-IP limiter, not resolution.
"""

import pytest
from fastapi.testclient import TestClient

import main
from main import app, get_resolver, require_api_key
from resolution import EmployerRecord, Resolution


class _StubResolver:
    """Stand-in for EmployerResolver: returns a fixed hit, no DB, no OpenAI."""

    def resolve(self, name: str) -> Resolution:
        return Resolution(
            record=EmployerRecord(employer_name="TEST CO", total_h1b_certified=1),
            match_type="exact",
            confidence=1.0,
        )


@pytest.fixture
def client():
    # Swap the prod wiring for stubs: the limiter is what we're exercising, not lookup.
    app.dependency_overrides[get_resolver] = lambda: _StubResolver()
    app.dependency_overrides[require_api_key] = lambda: None
    # Clear any in-memory counts so the test is deterministic regardless of run order.
    main.limiter.reset()
    main.limiter.enabled = True
    try:
        yield TestClient(app)
    finally:
        main.limiter.reset()
        app.dependency_overrides.clear()


def test_check_rate_limit_kicks_in_after_120(client):
    """First 120 requests from one IP succeed; the next ones get 429."""
    statuses = [
        client.get("/check", params={"company": f"acme {i}"}).status_code
        for i in range(130)
    ]

    assert statuses[:120] == [200] * 120, (
        f"first 120 should all be 200, got {sorted(set(statuses[:120]))}"
    )
    assert statuses[120:] == [429] * 10, (
        f"requests 121-130 should all be 429, got {statuses[120:]}"
    )


def test_429_body_signals_rate_limit(client):
    """Once throttled, the response is a 429 (not a masked 200)."""
    last = None
    for i in range(125):
        last = client.get("/check", params={"company": f"x {i}"})
    assert last.status_code == 429
