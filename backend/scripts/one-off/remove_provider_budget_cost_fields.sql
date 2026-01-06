-- One-off migration: Remove cost_per_input_token and cost_per_output_token from llm_providers
-- These fields are being removed because tracking token cost at the provider level is inaccurate.
-- Models have costs, not providers. Budget field is retained on provider.
--
-- Run this script against existing development databases after pulling the updated code.
-- The schema changes in the SQLAlchemy models remove these fields from new databases.
--
-- Usage: psql -d your_database -f remove_provider_budget_cost_fields.sql

-- Check if columns exist before dropping (PostgreSQL 9.6+)
DO $$
BEGIN
    -- llm_providers: Drop cost_per_input_token if it exists
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'llm_providers' AND column_name = 'cost_per_input_token'
    ) THEN
        ALTER TABLE llm_providers DROP COLUMN cost_per_input_token;
        RAISE NOTICE 'Dropped llm_providers.cost_per_input_token';
    ELSE
        RAISE NOTICE 'llm_providers.cost_per_input_token does not exist, skipping';
    END IF;

    -- llm_providers: Drop cost_per_output_token if it exists
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'llm_providers' AND column_name = 'cost_per_output_token'
    ) THEN
        ALTER TABLE llm_providers DROP COLUMN cost_per_output_token;
        RAISE NOTICE 'Dropped llm_providers.cost_per_output_token';
    ELSE
        RAISE NOTICE 'llm_providers.cost_per_output_token does not exist, skipping';
    END IF;
END $$;

-- Verify the changes
SELECT 'llm_providers' as table_name, column_name, data_type
FROM information_schema.columns
WHERE table_name = 'llm_providers'
ORDER BY ordinal_position;