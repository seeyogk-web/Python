-- Migration: 001_add_attempt_id.sql
-- Adds attempt_id column, backfills from results_data JSONB, and creates an index.
-- Backup your DB before running.

BEGIN;

-- 1) Add column (text) so backfill can proceed without casting errors
ALTER TABLE test_attempts
  ADD COLUMN IF NOT EXISTS attempt_id text;

-- 2) Backfill attempt_id from first element in results_data array that contains attempt_id
WITH extracted AS (
  SELECT id,
         (jsonb_array_elements(results_data) ->> 'attempt_id') AS attempt_id
  FROM test_attempts
)
UPDATE test_attempts t
SET attempt_id = e.attempt_id
FROM extracted e
WHERE t.id = e.id
  AND t.attempt_id IS NULL
  AND e.attempt_id IS NOT NULL;

-- 3) Create a text index for faster lookups
CREATE INDEX IF NOT EXISTS idx_test_attempts_attempt_id ON test_attempts(attempt_id);

COMMIT;

-- Optional: if all attempt_id values are valid UUIDs and you prefer a uuid-typed column,
-- verify validity first (see migration README or run the verification query). If all valid:
-- ALTER TABLE test_attempts ALTER COLUMN attempt_id TYPE uuid USING attempt_id::uuid;
-- Then recreate an index on the uuid column.
