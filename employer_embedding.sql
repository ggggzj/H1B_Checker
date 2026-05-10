-- Run once on Railway PostgreSQL (Query tab or `psql`) before using Layer 4 semantic /check.
-- Requires pgvector extension (available on Railway Postgres).

CREATE EXTENSION IF NOT EXISTS vector;

-- Add embedding column if it does not exist (safe to re-run)
ALTER TABLE employers
    ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- Optional: cosine ANN index after you have enough rows with non-null embeddings.
-- IVFFlat needs a training step; lists should scale with row count (see pgvector docs).
-- CREATE INDEX IF NOT EXISTS employers_embedding_ivfflat
--     ON employers USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
