"""
test_clean_data.py — Unit tests for the ETL normalization helpers.

Covers the dirty-data and normalization logic in clean_data.py that the
pipeline relies on before loading rows into PostgreSQL:

  is_empty                — null / NaN / placeholder detection
  normalize_employer      — canonical company name
  normalize_job_title     — canonical job title
  normalize_wage_level    — DOL wage level validation
  normalize_h1b_dependent — Y/N flag → bool/None
  parse_date              — Excel date / string → 'YYYY-MM-DD'

Run from repo root:
    pip install pytest
    pytest -v
"""

import math
from datetime import datetime, date

import pytest

from clean_data import (
    is_empty,
    normalize_employer,
    normalize_job_title,
    normalize_wage_level,
    normalize_h1b_dependent,
    parse_date,
)


# ─── is_empty: dirty-data detection ──────────────────────────────────────────

class TestIsEmpty:
    @pytest.mark.parametrize("value", [
        None,
        "",
        "   ",
        float("nan"),
        "nan",
        "NaN",
        "none",
        "N/A",
        "#N/A",
        "null",
    ])
    def test_treats_dirty_values_as_empty(self, value):
        assert is_empty(value) is True

    @pytest.mark.parametrize("value", [
        "Google",
        "GOOGLE LLC",
        "0",          # the string "0" is a real value, not empty
        0,            # the int 0 is a real value, not empty
        "n/a inc",    # contains n/a but is not exactly a placeholder
    ])
    def test_keeps_real_values(self, value):
        assert is_empty(value) is False


# ─── normalize_employer: canonical company name ──────────────────────────────

class TestNormalizeEmployer:
    def test_uppercases_and_strips(self):
        assert normalize_employer("  Google llc  ") == "GOOGLE LLC"

    def test_collapses_internal_whitespace(self):
        assert normalize_employer("GOOGLE    LLC") == "GOOGLE LLC"

    def test_strips_trailing_period_from_legal_suffix(self):
        assert normalize_employer("Acme Inc.") == "ACME INC"
        assert normalize_employer("Acme Corp.") == "ACME CORP"
        assert normalize_employer("Acme Ltd.") == "ACME LTD"

    def test_dirty_input_returns_none(self):
        assert normalize_employer(None) is None
        assert normalize_employer("   ") is None
        assert normalize_employer(float("nan")) is None

    def test_idempotent(self):
        once = normalize_employer("Microsoft Corp.")
        assert normalize_employer(once) == once


# ─── normalize_job_title: canonical job title ────────────────────────────────

class TestNormalizeJobTitle:
    def test_removes_parenthetical_content(self):
        assert normalize_job_title("Engineer (Java)") == "ENGINEER"

    def test_expands_sr_abbreviation(self):
        assert normalize_job_title("Sr. Software Engineer") == "SENIOR SOFTWARE ENGINEER"

    def test_expands_mgr_abbreviation(self):
        assert normalize_job_title("Product MGR") == "PRODUCT MANAGER"

    def test_dirty_input_returns_none(self):
        assert normalize_job_title("") is None
        assert normalize_job_title(None) is None


# ─── normalize_wage_level: DOL wage level validation ─────────────────────────

class TestNormalizeWageLevel:
    @pytest.mark.parametrize("level", ["I", "II", "III", "IV", " ii "])
    def test_accepts_valid_levels(self, level):
        assert normalize_wage_level(level) in {"I", "II", "III", "IV"}

    @pytest.mark.parametrize("level", ["V", "0", "high", "", None, "N/A"])
    def test_rejects_invalid_levels(self, level):
        assert normalize_wage_level(level) is None


# ─── normalize_h1b_dependent: Y/N → bool/None ────────────────────────────────

class TestNormalizeH1bDependent:
    def test_yes_is_true(self):
        assert normalize_h1b_dependent("Y") is True
        assert normalize_h1b_dependent("y") is True

    def test_no_is_false(self):
        assert normalize_h1b_dependent("N") is False

    def test_unknown_is_none(self):
        assert normalize_h1b_dependent("") is None
        assert normalize_h1b_dependent(None) is None
        assert normalize_h1b_dependent("maybe") is None


# ─── parse_date: Excel/string date → 'YYYY-MM-DD' ────────────────────────────

class TestParseDate:
    def test_python_datetime(self):
        assert parse_date(datetime(2025, 3, 14)) == "2025-03-14"

    def test_python_date(self):
        assert parse_date(date(2025, 3, 14)) == "2025-03-14"

    @pytest.mark.parametrize("raw,expected", [
        ("2025-03-14", "2025-03-14"),
        ("03/14/2025", "2025-03-14"),
        ("03-14-2025", "2025-03-14"),
        ("2025/03/14", "2025-03-14"),
    ])
    def test_string_formats(self, raw, expected):
        assert parse_date(raw) == expected

    def test_empty_returns_none(self):
        assert parse_date(None) is None
        assert parse_date("") is None
        assert parse_date(float("nan")) is None

    def test_unparseable_string_returned_as_is(self):
        # Better to keep the raw value than silently drop it.
        assert parse_date("not a date") == "not a date"
