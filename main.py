"""
FastAPI H1B Checker API
- GET /check?company=Google       → single-company lookup
- GET /search?q=amazon&limit=5    → fuzzy search (autocomplete)
"""

from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_
from fuzzywuzzy import fuzz
from database import init_db, get_db
from models import Employer, EmployerAlias
from pydantic import BaseModel
from typing import Optional, List

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
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

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

# ============ API routes ============

def _employer_to_check_response(employer: Employer) -> CheckResponse:
    """Convert an Employer ORM row into a CheckResponse dict."""
    return CheckResponse(
        found=True,
        employer_name=employer.employer_name,
        # Expose total_h1b_certified as h1b_count so the Chrome extension
        # doesn't need to change its field name
        h1b_count=employer.total_h1b_certified,
        sponsors_h1b=True,
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
    db: Session = Depends(get_db)
):
    """
    Check whether a company sponsors H1B.

    Lookup order:
      1. Exact match on employer_name
      2. Alias lookup (TRADE_NAME_DBA) → resolve to primary employer
      3. ILIKE substring match on employer_name

    Example: /check?company=Google
    """
    company_upper = company.strip().upper()

    # 1) Exact match on the canonical employer name
    employer = db.query(Employer).filter(
        Employer.employer_name == company_upper
    ).first()
    if employer:
        return _employer_to_check_response(employer)

    # 2) Alias lookup — check if the name is a known DBA / trade name
    alias = db.query(EmployerAlias).filter(
        EmployerAlias.alias_name == company_upper
    ).first()
    if alias:
        # Resolve alias → primary employer
        primary = db.query(Employer).filter(
            Employer.employer_name == alias.primary_employer_name
        ).first()
        if primary:
            return _employer_to_check_response(primary)

    # 3) Substring (ILIKE) match — handles partial names like "Google" → "GOOGLE LLC"
    fuzzy_match = db.query(Employer).filter(
        Employer.employer_name.ilike(f"%{company_upper}%")
    ).order_by(Employer.total_h1b_certified.desc()).first()
    if fuzzy_match:
        return _employer_to_check_response(fuzzy_match)

    # Not found in any lookup
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
    q_upper = q.strip().upper()

    # Fetch all employers whose name contains the keyword
    all_employers = db.query(Employer).filter(
        Employer.employer_name.ilike(f"%{q_upper}%")
    ).all()

    if not all_employers:
        return SearchResponse(results=[], total=0)

    # Score each result with fuzzywuzzy and sort by score descending
    scored = sorted(
        all_employers,
        key=lambda emp: fuzz.partial_ratio(q_upper, emp.employer_name),
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
            "health": "/health",
            "docs": "/docs"
        }
    }

if __name__ == "__main__":
    import os
    import uvicorn
    
    # Read port from environment variable (Railway will set this)
    port = int(os.getenv("PORT", 8000))
    
    # Run the server
    uvicorn.run(
        app, 
        host="0.0.0.0",  # Important! Must be 0.0.0.0 to receive external requests
        port=port
    )
