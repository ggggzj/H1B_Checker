"""
FastAPI H1B Checker API
- GET /check?company=Google       → single-company lookup
- GET /search?q=amazon&limit=5    → fuzzy search (autocomplete)
"""

import json
import logging
import math
import os
from functools import lru_cache
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fuzzywuzzy import fuzz
from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from clean_data import normalize_employer
from database import get_db
from models import Employer, EmployerAlias

app = FastAPI(
    title="H1B Checker API",
    description="Look up employers with certified H1B LCA data",
    version="1.0.0"
)

# Allow Chrome extensions and browser clients to call the API.
# Chrome extensions send Origin: chrome-extension://<id>
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_origins=[
        "https://www.linkedin.com",
        "https://linkedin.com",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_openai_client: Optional[OpenAI] = None


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _openai_client


@app.on_event("startup")
async def startup():
    """
    Application startup event.
    Note: Database initialization is handled separately.
    """
    print("✅ Application started successfully")
    pass

# ============ Pydantic response models ============

class CheckResponse(BaseModel):
    """Response for a single-company /check lookup."""
    found: bool
    employer_name: Optional[str] = None
    # Total unique certified H-1B LCA filings for this employer
    h1b_count: Optional[int] = None
    sponsors_h1b: bool
    # How the employer was resolved: exact | alias | fuzzy | semantic | invalid_input
    match_type: Optional[str] = None
    # 0.0–1.0; semantic (and damped ambiguous) may be < 1.0
    match_confidence: Optional[float] = None
    # Extra fields from the enriched employers table
    earliest_decision_date: Optional[str] = None
    latest_decision_date: Optional[str] = None
    last_active_year: Optional[int] = None
    h1b_dependent: Optional[bool] = None

    class Config:
        from_attributes = True

class SearchResult(BaseModel):
    """One result row returned by /search."""
    employer_name: str
    h1b_count: int

class SearchResponse(BaseModel):
    """Fuzzy search response."""
    results: List[SearchResult]
    total: int


class ExtensionSelectors(BaseModel):
    """DOM selectors served to the Chrome extension."""
    company_name: List[str]
    job_card: List[str]


class ExtensionConfigResponse(BaseModel):
    """Remote config for the LinkedIn content script."""
    version: str
    selectors: ExtensionSelectors


class SelectorMissReport(BaseModel):
    """Extension telemetry when company name extraction fails."""
    html: str = ""
    url: Optional[str] = None
    selectors_tried: Optional[List[str]] = None


# Remote extension config (also embedded in content.js as fallbacks).
# Primary company detection uses /company/{slug} URLs — CSS lists are backup only.
EXTENSION_CONFIG_VERSION = "1.0.6"
EXTENSION_COMPANY_SELECTORS: List[str] = [
    ".job-card-container__company-name",
    ".job-card-container__primary-description",
    "[class*='job-card-list__company-name']",
    ".job-card-list__entity-lockup .artdeco-entity-lockup__subtitle",
    ".artdeco-entity-lockup__subtitle div[dir='ltr']",
    ".artdeco-entity-lockup__subtitle",
    ".base-search-card__subtitle",
    ".base-card__subtitle",
    ".base-main-card__subtitle",
    ".jobs-unified-top-card__company-name",
    ".job-details-jobs-unified-top-card__company-name",
]
EXTENSION_JOB_CARD_SELECTORS: List[str] = [
    "[data-occludable-job-id]",
    ".jobs-search-results-list__list-item",
    "li.jobs-search-results__list-item",
    "li.scaffold-layout__list-item",
    ".job-card-list__entity-lockup",
    "[data-job-id]",
]

# ============ Semantic search (Layer 4 fallback) ====================


def _embedding_to_pg_vector_literal(values: list[float]) -> str:
    """Serialize embedding for PostgreSQL vector cast (JSON array syntax)."""
    return json.dumps([float(x) for x in values])


@lru_cache(maxsize=1000)
def _embedding_lru_cached(text_key: str) -> tuple[float, ...]:
    """
    OpenAI embedding for text_key, cached by exact string.
    Returns an immutable tuple so lru_cache can store it safely.
    Raises on API/network errors (not cached).
    """
    client = _get_openai_client()
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text_key,
    )
    emb = response.data[0].embedding
    return tuple(float(x) for x in emb)


def get_embedding_cached(text_key: str) -> Optional[list[float]]:
    """
    Embedding for normalized company name with LRU cache on success.
    Failures are not cached.
    """
    try:
        return list(_embedding_lru_cached(text_key))
    except Exception as e:
        print(f"⚠️  OpenAI API error (get_embedding_cached): {e}")
        return None


# Layer 4: fetch more neighbors than we return, then re-rank when scores bunch up.
_SEMANTIC_FETCH_LIMIT = 10
# When top-1 and top-2 cosine similarities are within this gap, break ties using
# certified LCA volume (similarity * log1p(h1b_count)).
_SIM_TIGHT_SCORE_GAP = 0.04
# Reject semantic hits that are "almost tied" with the runner-up (too noisy).
_AMBIGUITY_REJECT_MARGIN = 0.04


def _rerank_semantic_candidates(
    rows: list,
    similarity_threshold: float,
) -> list:
    """
    If the top vector hits are very close in score, prefer the employer with more
    certified filings (McKinsey-style: small shell vs main partnership).

    Never promotes a row whose similarity would fall below ``similarity_threshold``;
    in that case the original vector order is kept.
    """
    if len(rows) < 2:
        return rows
    s0 = float(rows[0]["similarity"])
    s1 = float(rows[1]["similarity"])
    if s0 - s1 >= _SIM_TIGHT_SCORE_GAP:
        return rows
    floor = max(similarity_threshold - 0.05, min(s0, s1) - 0.02)
    pool = [r for r in rows[:6] if float(r["similarity"]) >= floor]
    if len(pool) < 2:
        return rows
    best = max(
        pool,
        key=lambda r: float(r["similarity"])
        * math.log1p(max(int(r["total_h1b_certified"] or 0), 0)),
    )
    if float(best["similarity"]) < similarity_threshold:
        return rows
    if best["id"] == rows[0]["id"]:
        return rows
    return [best] + [r for r in rows if r["id"] != best["id"]]


def semantic_search(
    query: str,
    db: Session,
    similarity_threshold: float = 0.75,
    top_k: int = 3,
) -> Tuple[Optional[Employer], float, bool]:
    """
    pgvector cosine distance (<=>); similarity score = 1 - distance.

    Returns (best_employer | None, top1_similarity, is_ambiguous).

    Logs Layer 4 activity at INFO (flow / outcomes) and WARNING (failures)
    for monitoring and debugging. Enable with e.g. LOG_LEVEL=INFO on Railway.
    """
    logger.info(
        "semantic_search start query=%r fetch_limit=%s similarity_threshold=%s",
        query,
        _SEMANTIC_FETCH_LIMIT,
        similarity_threshold,
    )
    try:
        query_emb = get_embedding_cached(query)
        if not query_emb:
            logger.warning(
                "semantic_search skip query=%r reason=no_query_embedding",
                query,
            )
            return None, 0.0, False

        vec_lit = _embedding_to_pg_vector_literal(query_emb)
        sql = text(
            """
            SELECT
                id,
                employer_name,
                total_h1b_certified,
                1 - (embedding <=> CAST(:emb AS vector)) AS similarity
            FROM employers
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT :k
            """
        )
        results = list(
            db.execute(
                sql, {"emb": vec_lit, "k": _SEMANTIC_FETCH_LIMIT}
            ).mappings().all()
        )

        if not results:
            logger.info(
                "semantic_search miss query=%r reason=no_rows_with_embedding",
                query,
            )
            return None, 0.0, False

        results = _rerank_semantic_candidates(results, similarity_threshold)

        top1_score = float(results[0]["similarity"])
        is_ambiguous = False
        margin: Optional[float] = None
        if len(results) > 1:
            top2_score = float(results[1]["similarity"])
            margin = top1_score - top2_score
            if margin < 0.1:
                is_ambiguous = True

        preview = [
            {
                "id": int(r["id"]),
                "employer_name": r["employer_name"],
                "similarity": round(float(r["similarity"]), 4),
            }
            for r in results[: max(top_k, 5)]
        ]
        logger.info(
            "semantic_search candidates query=%r top=%s ambiguous=%s margin=%s",
            query,
            preview,
            is_ambiguous,
            round(margin, 4) if margin is not None else None,
        )

        if top1_score < similarity_threshold:
            logger.info(
                "semantic_search miss query=%r reason=below_threshold "
                "top1_similarity=%.4f threshold=%.2f ambiguous=%s",
                query,
                top1_score,
                similarity_threshold,
                is_ambiguous,
            )
            return None, top1_score, is_ambiguous

        if is_ambiguous and margin is not None and margin < _AMBIGUITY_REJECT_MARGIN:
            logger.info(
                "semantic_search miss query=%r reason=ambiguous_tight_margin "
                "top1_similarity=%.4f margin=%.4f",
                query,
                top1_score,
                margin,
            )
            return None, top1_score, True

        best_match = db.query(Employer).filter(Employer.id == results[0]["id"]).first()
        logger.info(
            "semantic_search hit query=%r employer_id=%s employer_name=%r "
            "similarity=%.4f ambiguous=%s",
            query,
            results[0]["id"],
            results[0]["employer_name"],
            top1_score,
            is_ambiguous,
        )
        return best_match, top1_score, is_ambiguous

    except Exception as e:
        logger.warning("semantic_search error query=%r err=%s", query, e, exc_info=True)
        return None, 0.0, False


# ============ API routes ============

def _employer_to_check_response(
    employer: Employer,
    match_type: str = "exact",
    match_confidence: float = 1.0,
) -> CheckResponse:
    """Convert an Employer ORM row into a CheckResponse dict."""
    return CheckResponse(
        found=True,
        employer_name=employer.employer_name,
        # Expose total_h1b_certified as h1b_count so the Chrome extension
        # doesn't need to change its field name
        h1b_count=employer.total_h1b_certified,
        sponsors_h1b=True,
        match_type=match_type,
        match_confidence=match_confidence,
        earliest_decision_date=(
            str(employer.earliest_decision_date)
            if employer.earliest_decision_date else None
        ),
        latest_decision_date=(
            str(employer.latest_decision_date)
            if employer.latest_decision_date else None
        ),
        last_active_year=employer.last_active_year,
        h1b_dependent=employer.h1b_dependent,
    )


@app.get("/check", response_model=CheckResponse)
async def check_employer(
    company: str = Query(..., min_length=1, description="Company name"),
    db: Session = Depends(get_db),
):
    """
    Check whether a company sponsors H1B (four-layer resolution).

    Lookup order:
      1. Exact match on employer_name (normalized)
      2. Alias lookup (TRADE_NAME_DBA) → primary employer
      3. ILIKE substring match on employer_name
      4. Semantic search (pgvector) when 1–3 miss

    Example: /check?company=Google
    """
    company_normalized = normalize_employer(company)
    if not company_normalized:
        return CheckResponse(
            found=False,
            sponsors_h1b=False,
            match_type="invalid_input",
            match_confidence=None,
        )

    # 1) Exact match on the canonical employer name
    employer = db.query(Employer).filter(
        Employer.employer_name == company_normalized
    ).first()
    if employer:
        return _employer_to_check_response(employer, match_type="exact")

    # 2) Alias lookup — check if the name is a known DBA / trade name
    alias = db.query(EmployerAlias).filter(
        EmployerAlias.alias_name == company_normalized
    ).first()
    if alias:
        primary = db.query(Employer).filter(
            Employer.employer_name == alias.primary_employer_name
        ).first()
        if primary:
            return _employer_to_check_response(primary, match_type="alias")

    # 3) Substring (ILIKE) match — handles partial names like "Google" → "GOOGLE LLC"
    fuzzy_match = db.query(Employer).filter(
        Employer.employer_name.ilike(f"%{company_normalized}%")
    ).order_by(Employer.total_h1b_certified.desc()).first()
    if fuzzy_match:
        return _employer_to_check_response(fuzzy_match, match_type="fuzzy")

    # 4) Semantic fallback (requires OPENAI_API_KEY + pgvector embeddings on rows)
    semantic_match, confidence, is_ambiguous = semantic_search(
        company_normalized,
        db,
        similarity_threshold=0.75,
    )
    if semantic_match:
        match_confidence = confidence * 0.9 if is_ambiguous else confidence
        return _employer_to_check_response(
            semantic_match,
            match_type="semantic",
            match_confidence=match_confidence,
        )

    return CheckResponse(found=False, sponsors_h1b=False)


@app.get("/search", response_model=SearchResponse)
async def search_employers(
    q: str = Query(..., min_length=1, description="Search keyword"),
    limit: int = Query(5, ge=1, le=50, description="Max results"),
    db: Session = Depends(get_db)
):
    """
    Fuzzy search employers (for autocomplete).

    Example: /search?q=amazon&limit=5
    """
    q_norm = normalize_employer(q)
    if not q_norm:
        return SearchResponse(results=[], total=0)

    # Fetch all employers whose name contains the keyword
    all_employers = db.query(Employer).filter(
        Employer.employer_name.ilike(f"%{q_norm}%")
    ).all()

    if not all_employers:
        return SearchResponse(results=[], total=0)

    # Score each result with fuzzywuzzy and sort by score descending
    scored = sorted(
        all_employers,
        key=lambda emp: fuzz.partial_ratio(q_norm, emp.employer_name),
        reverse=True,
    )

    results = [
        SearchResult(
            employer_name=emp.employer_name,
            h1b_count=emp.total_h1b_certified,
        )
        for emp in scored[:limit]
    ]

    return SearchResponse(results=results, total=len(results))

# ============ Extension config & telemetry ============

@app.get("/config", response_model=ExtensionConfigResponse)
async def get_extension_config():
    """
    Remote DOM selectors for the Chrome extension.

    When LinkedIn changes markup, update EXTENSION_COMPANY_SELECTORS here so
    installed clients can pick up new selectors without a store release.
    """
    return ExtensionConfigResponse(
        version=EXTENSION_CONFIG_VERSION,
        selectors=ExtensionSelectors(
            company_name=EXTENSION_COMPANY_SELECTORS,
            job_card=EXTENSION_JOB_CARD_SELECTORS,
        ),
    )


@app.post("/report-selector-miss")
async def report_selector_miss(report: SelectorMissReport):
    """
    Log when the extension cannot extract a company name from a job card.
    Helps detect LinkedIn DOM changes early.
    """
    snippet = (report.html or "")[:500]
    logger.warning(
        "[SELECTOR MISS] url=%s selectors=%s snippet=%s",
        report.url,
        report.selectors_tried,
        snippet,
    )
    return {"ok": True}


# ============ Health ============

@app.get("/health")
async def health_check():
    """Health check."""
    return {"status": "healthy", "service": "H1B Checker API"}

@app.get("/")
async def root():
    """Root: API metadata."""
    return {
        "name": "H1B Checker API",
        "version": "1.0.0",
        "endpoints": {
            "check": "/check?company=Google",
            "search": "/search?q=amazon&limit=5",
            "config": "/config",
            "health": "/health",
            "docs": "/docs"
        }
    }

if __name__ == "__main__":
    import uvicorn

    # Read port from environment variable (Railway will set this)
    port = int(os.getenv("PORT", 8000))

    # Run the server
    uvicorn.run(
        app,
        host="0.0.0.0",  # Important! Must be 0.0.0.0 to receive external requests
        port=port
    )

# ==================== Testing Checklist ====================
# Layer 1 - Exact:
#   curl "localhost:8000/check?company=GOOGLE LLC"
#   Expected: {"found": true, "match_type": "exact", "match_confidence": 1.0}
#
# Layer 2 - Alias:
#   curl "localhost:8000/check?company=GOOGLE"  # if DBA table has this alias
#   Expected: {"found": true, "match_type": "alias"}
#
# Layer 3 - Fuzzy:
#   curl "localhost:8000/check?company=Google"
#   Expected: {"found": true, "match_type": "fuzzy"}
#
# Layer 4 - Semantic:
#   curl "localhost:8000/check?company=Facebook"
#   Expected: {"found": true, "employer_name": "META PLATFORMS INC",
#             "match_type": "semantic", "match_confidence": ~0.8+}
#
#   curl "localhost:8000/check?company=Alphabet"
#   Expected: {"employer_name": "GOOGLE LLC", "match_type": "semantic"}
#
#   curl "localhost:8000/check?company=PWC"
#   Expected: {"employer_name": "PRICEWATERHOUSECOOPERS LLP"}
#
# Edge Cases:
#   curl "localhost:8000/check?company="  # rejected by FastAPI (min_length)
#   curl "gibberish" normalized empty → invalid_input if applicable
#
#   curl "localhost:8000/check?company=NonExistentCompany12345"
#   Expected: {"found": false}
