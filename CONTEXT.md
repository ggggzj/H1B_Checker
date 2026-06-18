# H1B Checker — Context

Domain and architecture vocabulary for the H-1B sponsorship checker: a Python intake
pipeline feeds the employer table, a FastAPI app answers `/check`, and a Chrome extension
annotates LinkedIn. Use these terms exactly; pick the listed word over the alternatives.

## Domain

**Employer**:
One canonical company in the employer table, keyed by a normalized name, with aggregated
certified H-1B LCA stats.
_Avoid_: company, organization, firm.

**Alias**:
A trade name / DBA that maps to a primary employer. One employer may have many aliases.
_Avoid_: DBA (in code), alternate name.

**Intake**:
The pipeline that turns raw DOL LCA Excel into the canonical employer table
(`clean_data` → CSVs → `upload_to_railway`).
_Avoid_: ETL, ingestion, import.

**Normalized name**:
The canonical form of a company name (uppercase, collapsed whitespace, stripped legal-suffix
periods) produced by `normalize_employer`. The single key everything matches on.
_Avoid_: clean name, canonical string.

## Resolution

**Resolution**:
Turning a raw, user-typed company string into one best `Employer`. The product's core
behaviour, owned by the resolution module.
_Avoid_: lookup, matching, search (search means the multi-result `/search`).

**Match type**:
How a resolution was reached: `exact`, `alias`, `fuzzy`, `semantic`, plus `invalid_input`
and `miss`. Tried strictly in that order; the first hit wins.
_Avoid_: strategy, layer name, tier.

**EmployerRecord**:
The pure value object the resolution module works in — a flat projection of an employer row
carrying only the fields a response needs. Never a live ORM object.
_Avoid_: DTO, row, model.

**EmployerRepo**:
The seam over the employer table: `find_exact`, `find_by_alias`, `find_fuzzy`,
`vector_search`. Postgres adapter in prod, in-memory fake in tests.
_Avoid_: DAO, store, repository (full word) — say repo.

**Embedder**:
The seam over the embedding provider: `embed(text) -> vector | None`. OpenAI adapter in
prod, deterministic fake in tests. Returns `None` on failure rather than raising.
_Avoid_: embedding client, vectorizer.
