"""
FastAPI H1B Checker API
- GET /check?company=Google       → single-company lookup
- GET /search?q=amazon&limit=5    → fuzzy search (autocomplete)
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Depends, Query, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fuzzywuzzy import fuzz
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from clean_data import normalize_employer
from database import get_db
from models import Employer
from resolution import EmployerResolver, OpenAIEmbedder, Resolution, SqlEmployerRepo

app = FastAPI(
    title="H1B Checker API",
    description="Look up employers with certified H1B LCA data",
    version="1.0.0"
)

# ============ Rate limiting (denial-of-wallet protection) ============
# Cap the expensive endpoints per client IP. /check can reach OpenAI in Layer 4
# (semantic search) and /search scans the table, so an attacker hammering random
# names would otherwise burn the OpenAI budget and CPU.
#
# We key by IP, NOT by API key: the extension ships ONE shared key for every
# install, so keying by key would lump all users into a single bucket and throttle
# everyone at once. Each real user has their own IP, so per-IP limits isolate an
# abuser to their own address.
#
# Storage is in-memory (per process) — fine for a single Railway instance. If you
# scale to multiple instances/workers, the counts won't be shared; switch to a
# common store via Limiter(storage_uri="redis://...").


def _client_ip(request: Request) -> str:
    """Real client IP, honoring the X-Forwarded-For header Railway sits behind."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First entry is the original client; later entries are proxies.
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_client_ip)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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

# ============ API key auth (denial-of-wallet protection) ============
# Set API_KEY in the server environment (Railway → Variables) to require callers
# to send a matching X-API-Key header. The Chrome extension sends this header via
# background.js. Protects the OpenAI-backed /check and the heavy /search.
#
# NOTE: When API_KEY is unset, auth is DISABLED (open) so deploying this code does
# not lock out the service before the env var is set. You MUST set API_KEY on the
# server to actually be protected. The key shipped inside the extension is only weak
# protection (extensions can be unpacked) — pair it with rate limiting + the OpenAI
# budget cap for real safety.
API_KEY = os.environ.get("API_KEY")


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency: reject requests without a valid X-API-Key header."""
    if not API_KEY:
        logger.warning(
            "require_api_key: API_KEY env not set — auth is DISABLED (open endpoint)"
        )
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


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

# Default semantic similarity threshold for the resolution module's Layer 4.
# NOTE: git history (c2c3b19) intended to lower this from 0.75 to 0.65, but the deployed
# code kept 0.75. Override per-environment with SEMANTIC_THRESHOLD without a redeploy.
SEMANTIC_THRESHOLD = float(os.getenv("SEMANTIC_THRESHOLD", "0.75"))


# ============ Resolution wiring ============

def get_resolver(db: Session = Depends(get_db)) -> EmployerResolver:
    """Single composition point: wire the Postgres + OpenAI adapters behind the seams."""
    return EmployerResolver(
        SqlEmployerRepo(db),
        OpenAIEmbedder(),
        similarity_threshold=SEMANTIC_THRESHOLD,
    )


def to_check_response(res: Resolution) -> CheckResponse:
    """Map a Resolution to the HTTP response the Chrome extension expects."""
    if res.record is None:
        return CheckResponse(
            found=False,
            sponsors_h1b=False,
            # Echo "invalid_input" so callers can tell a rejected query from a clean miss.
            match_type=res.match_type if res.match_type == "invalid_input" else None,
            match_confidence=res.confidence,
        )
    e = res.record
    return CheckResponse(
        found=True,
        employer_name=e.employer_name,
        # Expose total_h1b_certified as h1b_count so the Chrome extension
        # doesn't need to change its field name
        h1b_count=e.total_h1b_certified,
        sponsors_h1b=True,
        match_type=res.match_type,
        match_confidence=res.confidence,
        earliest_decision_date=e.earliest_decision_date,
        latest_decision_date=e.latest_decision_date,
        last_active_year=e.last_active_year,
        h1b_dependent=e.h1b_dependent,
    )


# ============ API routes ============


@app.get("/check", response_model=CheckResponse)
@limiter.limit("120/minute;2000/hour")
async def check_employer(
    request: Request,
    company: str = Query(..., min_length=1, description="Company name"),
    resolver: EmployerResolver = Depends(get_resolver),
    _: None = Depends(require_api_key),
):
    """
    Check whether a company sponsors H1B (four-layer resolution).

    Lookup order: exact → alias (DBA) → ILIKE substring → semantic (pgvector).

    Example: /check?company=Google
    """
    return to_check_response(resolver.resolve(company))


@app.get("/search", response_model=SearchResponse)
@limiter.limit("30/minute;300/hour")
async def search_employers(
    request: Request,
    q: str = Query(..., min_length=1, description="Search keyword"),
    limit: int = Query(5, ge=1, le=50, description="Max results"),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
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


# ============ Ask (RAG - step 1: just say hello) ============

@app.get("/ask")
async def ask(question: str = Query(..., description="A question from the user")):
    """Step 1: returns a fixed reply. No AI yet. We test that it works first."""
    return {
        "question": question,
        "answer": "Hello! The /ask endpoint works. AI is not added yet.",
    }


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
