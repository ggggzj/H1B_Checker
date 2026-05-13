"""
Offline batch job: fill employers.embedding for semantic /check (Layer 4).

Reads employers with NULL embedding, requests OpenAI text-embedding-3-small
vectors in batches of BATCH_SIZE, writes back to PostgreSQL, then creates
an HNSW index for cosine similarity search.

Run from repository root:
    python scripts/precompute_embeddings.py

Requires:
    - DATABASE_URL in .env (same as database.py)
    - OPENAI_API_KEY
    - pgvector extension and employers.embedding vector(1536) column
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow imports of project modules when executed as scripts/precompute_embeddings.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import os
from dotenv import load_dotenv

# Load env before database/models so DATABASE_URL / OPENAI_API_KEY work even when
# cwd is not the repo root (database.py only calls load_dotenv() with default cwd).
_env_file = _REPO_ROOT / ".env"
load_dotenv(_env_file)
load_dotenv()

from openai import OpenAI
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from database import SessionLocal, engine
from models import Employer

# --- Configuration ---------------------------------------------------------

BATCH_SIZE: int = 100
EMBEDDING_MODEL: str = "text-embedding-3-small"
# OpenAI list price for text-embedding-3-small (verify on pricing page periodically)
USD_PER_1M_INPUT_TOKENS: float = 0.02


def _require_api_key() -> None:
    """Exit immediately if OPENAI_API_KEY is missing."""
    if not os.getenv("OPENAI_API_KEY"):
        env_path = _REPO_ROOT / ".env"
        raise SystemExit(
            "ERROR: OPENAI_API_KEY is not set.\n"
            f"  Add it to {env_path} (create the file if needed), for example:\n"
            "    OPENAI_API_KEY=sk-...\n"
            "  Or export it in the shell: export OPENAI_API_KEY=sk-..."
        )


def _estimate_cost_usd(estimated_tokens: int) -> float:
    return (estimated_tokens / 1_000_000.0) * USD_PER_1M_INPUT_TOKENS


def _count_stats(session: Session) -> tuple[int, int, int]:
    """
    Return (total_employers, pending_null_embedding, completed_with_embedding).
    """
    total = session.query(func.count(Employer.id)).scalar() or 0
    pending = (
        session.query(func.count(Employer.id))
        .filter(Employer.embedding.is_(None))
        .scalar()
        or 0
    )
    done = total - pending
    return int(total), int(pending), int(done)


def _fetch_next_batch(session: Session, limit: int) -> list[Employer]:
    """Return up to `limit` employers where embedding IS NULL, ordered by id."""
    return (
        session.query(Employer)
        .filter(Employer.embedding.is_(None))
        .order_by(Employer.id)
        .limit(limit)
        .all()
    )


def _embed_batch_openai(
    client: OpenAI, names: list[str]
) -> tuple[list[list[float] | None], int]:
    """
    Request embeddings for all names in one API call.

    Returns (list of vectors aligned with names, prompt_tokens from usage).
    On total failure returns list of Nones and 0 tokens.
    """
    if not names:
        return [], 0
    try:
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=names)
        tokens = 0
        if response.usage and response.usage.total_tokens is not None:
            tokens = int(response.usage.total_tokens)
        vectors: list[list[float] | None] = []
        for item in response.data:
            vectors.append(list(map(float, item.embedding)))
        if len(vectors) != len(names):
            print(
                f"⚠️  OpenAI returned {len(vectors)} embeddings for {len(names)} inputs; "
                "falling back to per-row API calls."
            )
            return _embed_rows_fallback(client, names)
        return vectors, tokens
    except Exception as e:
        print(f"⚠️  OpenAI batch embedding failed ({len(names)} rows): {e}")
        print("   → Retrying one company per request...")
        return _embed_rows_fallback(client, names)


def _embed_rows_fallback(
    client: OpenAI, names: list[str]
) -> tuple[list[list[float] | None], int]:
    """Per-row OpenAI calls so a single bad row does not lose the whole batch."""
    vectors: list[list[float] | None] = [None] * len(names)
    total_tokens = 0
    for i, name in enumerate(names):
        try:
            response = client.embeddings.create(model=EMBEDDING_MODEL, input=name)
            if response.usage and response.usage.total_tokens is not None:
                total_tokens += int(response.usage.total_tokens)
            vectors[i] = list(map(float, response.data[0].embedding))
        except Exception as e:
            print(f"⚠️  OpenAI skip row [{i + 1}/{len(names)}] {name!r}: {e}")
            vectors[i] = None
    return vectors, total_tokens


def _persist_embeddings(
    session: Session,
    employers: list[Employer],
    vectors: list[list[float] | None],
) -> int:
    """
    Write vectors to the database. Commits per row so one failure does not
    poison the whole session; DB errors are logged and the next row is still
    attempted.

    Prints progress every 10 successful writes (denominator = batch size).

    Returns number of rows successfully updated.
    """
    saved = 0
    batch_len = len(employers)
    last_name: str | None = None

    for emp, vec in zip(employers, vectors):
        if vec is None:
            continue
        if len(vec) != 1536:
            print(
                f"⚠️  DB skip {emp.employer_name!r}: expected 1536 dims, got {len(vec)}"
            )
            continue
        try:
            emp.embedding = vec
            session.add(emp)
            session.commit()
            saved += 1
            last_name = emp.employer_name
            if saved % 10 == 0:
                print(f"   ✅ [{saved}/{batch_len}] {emp.employer_name}")
        except Exception as e:
            session.rollback()
            print(f"⚠️  DB error for id={emp.id} {emp.employer_name!r}: {e}")

    if saved > 0 and saved % 10 != 0 and last_name is not None:
        print(f"   ✅ [{saved}/{batch_len}] {last_name}")

    return saved


def ensure_embedding_schema() -> None:
    """
    Ensure PostgreSQL has the pgvector extension and employers.embedding column.

    Matches employer_embedding.sql so local DBs created before that migration
    still work when running this script. Safe to re-run (IF NOT EXISTS).
    """
    print("📦 Checking pgvector extension and employers.embedding column...")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as e:
        print(f"   ⚠️  CREATE EXTENSION vector: {e}")
        print("      (Safe to ignore if the extension already exists or the role lacks permission;")
        print("       if the ALTER below also fails, run it as a superuser instead.)")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE employers ADD COLUMN IF NOT EXISTS "
                    "embedding vector(1536)"
                )
            )
    except Exception as e:
        raise SystemExit(
            f"ERROR: failed to add column employers.embedding: {e}\n"
            "  Run employer_embedding.sql (at repo root) manually against the database,\n"
            "  or install the vector extension using a privileged role."
        ) from e
    print("   ✅ employers.embedding is ready (created or already existed).")
    print()


def _print_banner(total: int, done: int, pending: int) -> None:
    # Rough token estimate: ~5 input tokens per employer name on average (tune if needed)
    est_tokens = pending * 5
    est_cost = _estimate_cost_usd(est_tokens)
    print()
    print("=" * 60)
    print("🚀 Embedding precompute script")
    print("=" * 60)
    print(f"   Total employers:   {total:,}")
    print(f"   Already embedded:  {done:,}")
    print(f"   Pending:           {pending:,}")
    print(f"   Estimated cost:    ${est_cost:.4f}")
    print("=" * 60)
    print()


def create_hnsw_index() -> None:
    """
    Create an HNSW index for cosine distance on employers.embedding.

    Uses a partial index so rows with NULL embedding are excluded. Safe to
    re-run thanks to IF NOT EXISTS.
    """
    ddl = text(
        """
        CREATE INDEX IF NOT EXISTS employers_embedding_hnsw_idx
        ON employers
        USING hnsw (embedding vector_cosine_ops)
        WHERE (embedding IS NOT NULL)
        """
    )
    print("🔧 Creating HNSW index...")
    try:
        with engine.begin() as conn:
            conn.execute(ddl)
        print("   ✅ Index created")
    except Exception as e:
        print(f"   ⚠️  Failed to create index (check pgvector version for HNSW support): {e}")


def run() -> None:
    """
    Main entry: process all NULL-embedding employers in batches, then build HNSW.
    """
    ensure_embedding_schema()
    _require_api_key()
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    session = SessionLocal()
    try:
        total, pending_initial, done_initial = _count_stats(session)
        _print_banner(total, done_initial, pending_initial)

        if pending_initial == 0:
            print("No pending employers (every row already has an embedding). Skipping OpenAI calls.")
            create_hnsw_index()
            print()
            print("=" * 60)
            print("✅ All done! (no embeddings needed to be written)")
            print("=" * 60)
            return

        batch_no = 0
        cumulative_processed = 0
        cumulative_tokens = 0
        cumulative_cost = 0.0

        while True:
            batch = _fetch_next_batch(session, BATCH_SIZE)
            if not batch:
                break

            batch_no += 1
            names = [e.employer_name for e in batch]
            print(f"📊 Batch {batch_no} ({len(batch)} employers):")

            vectors, batch_tokens = _embed_batch_openai(client, names)
            cumulative_tokens += batch_tokens
            cumulative_cost += _estimate_cost_usd(batch_tokens)

            saved = _persist_embeddings(session, batch, vectors)

            cumulative_processed += saved
            print(f"   💾 Committed Batch {batch_no} ({saved} / {len(batch)} rows written)")
            print(
                f"   📈 Overall progress: {cumulative_processed:,} / {pending_initial:,} "
                f"({100.0 * cumulative_processed / max(pending_initial, 1):.1f}%)"
            )
            print(f"   💰 Cumulative cost (estimated from tokens): ${cumulative_cost:.4f}")
            print()

        create_hnsw_index()
        print()
        print("=" * 60)
        print("✅ All done!")
        print(f"   Embeddings written this run: {cumulative_processed:,} employers")
        print(f"   Tokens used this run (reported by OpenAI): {cumulative_tokens:,}")
        print(f"   Estimated cost this run: ${cumulative_cost:.4f}")
        print("=" * 60)

    finally:
        session.close()


if __name__ == "__main__":
    run()
