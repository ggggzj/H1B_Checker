"""
Fast bulk backfill for employers.embedding.

Same goal as scripts/precompute_embeddings.py, but writes each batch with a
single bulk UPDATE (one network round-trip per batch) instead of committing
per row. On a remote DB the per-row commit was the bottleneck (~1.25s/row);
bulk update brings the whole backfill down to a couple of minutes.

Run from repo root:  python scripts/backfill_embeddings_fast.py
Requires: DATABASE_URL + OPENAI_API_KEY in .env, pgvector, employers.embedding.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")
load_dotenv()

from openai import OpenAI
from sqlalchemy import create_engine, text

EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_BATCH = 500          # names per OpenAI request
EXPECTED_DIM = 1536

HNSW_DDL = """
CREATE INDEX IF NOT EXISTS employers_embedding_hnsw_idx
ON employers USING hnsw (embedding vector_cosine_ops)
WHERE (embedding IS NOT NULL)
"""


def _engine():
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, connect_args={"connect_timeout": 15})


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _bulk_update(conn, pairs: list[tuple[int, str]]) -> int:
    """One UPDATE for the whole batch via a VALUES list. Returns row count."""
    values = ",".join(f"(:id{i}, :v{i})" for i in range(len(pairs)))
    sql = text(
        f"""
        UPDATE employers AS e
        SET embedding = d.v::vector
        FROM (VALUES {values}) AS d(id, v)
        WHERE e.id = d.id::int
        """
    )
    params: dict = {}
    for i, (eid, vlit) in enumerate(pairs):
        params[f"id{i}"] = eid
        params[f"v{i}"] = vlit
    res = conn.execute(sql, params)
    return res.rowcount or 0


def run() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set in .env")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    engine = _engine()

    with engine.connect() as conn:
        total = conn.execute(text("SELECT count(*) FROM employers")).scalar() or 0
        pending = conn.execute(
            text("SELECT count(*) FROM employers WHERE embedding IS NULL")
        ).scalar() or 0
    print(f"Total {total:,} | pending {pending:,}")
    if pending == 0:
        print("Nothing to embed.")
    written = 0
    batch_no = 0
    while True:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, employer_name FROM employers "
                    "WHERE embedding IS NULL ORDER BY id LIMIT :lim"
                ),
                {"lim": OPENAI_BATCH},
            ).all()
        if not rows:
            break
        batch_no += 1
        ids = [r[0] for r in rows]
        names = [r[1] or "" for r in rows]

        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=names)
        vectors = [list(map(float, d.embedding)) for d in resp.data]

        pairs = [
            (eid, _vec_literal(v))
            for eid, v in zip(ids, vectors)
            if len(v) == EXPECTED_DIM
        ]
        with engine.begin() as conn:
            n = _bulk_update(conn, pairs)
        written += n
        print(f"  batch {batch_no}: wrote {n} (total {written:,}/{pending:,})", flush=True)

    print("Rebuilding HNSW index...")
    with engine.begin() as conn:
        conn.execute(text(HNSW_DDL))
    with engine.connect() as conn:
        left = conn.execute(
            text("SELECT count(*) FROM employers WHERE embedding IS NULL")
        ).scalar()
    print(f"Done. embeddings written this run: {written:,} | remaining NULL: {left:,}")


if __name__ == "__main__":
    run()
