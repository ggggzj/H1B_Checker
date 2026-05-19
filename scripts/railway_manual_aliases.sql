-- Run in Railway → PostgreSQL → **Query** (or Data → Query) if your laptop
-- cannot reach the DB (timeout / firewall). This only touches curated aliases.
--
-- Safe to re-run: removes prior MANUAL_CURATED rows, then inserts the set from
-- curated/manual_employer_aliases.csv (keep in sync when you edit that file).

DELETE FROM employer_aliases WHERE alias_type = 'MANUAL_CURATED';

INSERT INTO employer_aliases (primary_employer_name, alias_name, alias_type, usage_count) VALUES
('META PLATFORMS, INC', 'FACEBOOK', 'MANUAL_CURATED', 0),
('META PLATFORMS, INC', 'INSTAGRAM', 'MANUAL_CURATED', 0),
('META PLATFORMS, INC', 'WHATSAPP', 'MANUAL_CURATED', 0),
('GOOGLE LLC', 'ALPHABET', 'MANUAL_CURATED', 0),
('SNAP INC', 'SNAP', 'MANUAL_CURATED', 0),
('PRICEWATERHOUSECOOPERS LLP', 'PWC', 'MANUAL_CURATED', 0),
('JPMORGAN CHASE & CO', 'JP MORGAN', 'MANUAL_CURATED', 0),
('GOLDMAN SACHS & CO LLC', 'GOLDMAN', 'MANUAL_CURATED', 0),
('MCKINSEY & COMPANY, INC UNITED STATES', 'MCKINSEY', 'MANUAL_CURATED', 0);
