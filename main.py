"""
FastAPI H1B Checker API
- GET /check?company=Google       → single-company lookup
- GET /search?q=amazon&limit=5    → fuzzy search (autocomplete)
"""

from fastapi import FastAPI, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from fuzzywuzzy import fuzz
from database import init_db, get_db
from models import Employer
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI(
    title="H1B Checker API",
    description="Look up employers with certified H1B LCA data",
    version="1.0.0"
)

@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    init_db()
    print("✅ FastAPI server started")

# ============ Pydantic response models ============

class CheckResponse(BaseModel):
    """Response for a single-company check."""
    found: bool
    employer_name: Optional[str] = None
    h1b_count: Optional[int] = None
    sponsors_h1b: bool

    class Config:
        from_attributes = True

class SearchResult(BaseModel):
    """One search result row."""
    employer_name: str
    h1b_count: int

class SearchResponse(BaseModel):
    """Fuzzy search response."""
    results: List[SearchResult]
    total: int

# ============ API routes ============

@app.get("/check", response_model=CheckResponse)
async def check_employer(
    company: str = Query(..., min_length=1, description="Company name"),
    db: Session = Depends(get_db)
):
    """
    Check whether a company sponsors H1B (certified LCA data).

    Example: /check?company=Google
    """
    company_upper = company.strip().upper()
    
    # 1) Exact match
    employer = db.query(Employer).filter(
        Employer.employer_name == company_upper
    ).first()
    
    if employer:
        return CheckResponse(
            found=True,
            employer_name=employer.employer_name,
            h1b_count=employer.h1b_count,
            sponsors_h1b=True
        )
    
    # 2) Fuzzy match (ILIKE)
    fuzzy_match = db.query(Employer).filter(
        Employer.employer_name.ilike(f"%{company_upper}%")
    ).first()
    
    if fuzzy_match:
        return CheckResponse(
            found=True,
            employer_name=fuzzy_match.employer_name,
            h1b_count=fuzzy_match.h1b_count,
            sponsors_h1b=True
        )
    
    # Not found
    return CheckResponse(
        found=False,
        sponsors_h1b=False
    )

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
    
    # Employers whose name contains the keyword
    all_employers = db.query(Employer).filter(
        Employer.employer_name.ilike(f"%{q_upper}%")
    ).all()
    
    if not all_employers:
        return SearchResponse(results=[], total=0)
    
    # Score with fuzzywuzzy and sort
    scored_employers = [
        (employer, fuzz.partial_ratio(q_upper, employer.employer_name))
        for employer in all_employers
    ]
    
    # Sort by score descending
    scored_employers.sort(key=lambda x: x[1], reverse=True)
    
    # Top `limit` results
    top_results = scored_employers[:limit]
    
    results = [
        SearchResult(
            employer_name=emp.employer_name,
            h1b_count=emp.h1b_count
        )
        for emp, score in top_results
    ]
    
    return SearchResponse(
        results=results,
        total=len(results)
    )

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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
