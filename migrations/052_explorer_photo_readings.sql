-- Extend the shared structured Explorer ledger for photo based readings.
-- Images are never stored in this table; only validated input metadata and
-- the structured interpretation are persisted.
ALTER TABLE explorer_structured_readings
    DROP CONSTRAINT IF EXISTS explorer_structured_type_valid;

ALTER TABLE explorer_structured_readings
    ADD CONSTRAINT explorer_structured_type_valid
    CHECK (experience_type IN ('dreams', 'compatibility', 'palm', 'coffee'));
