-- Shu RAG Backend Database Initialization Script
-- This script sets up a PostgreSQL database for Shu with all required extensions and configurations
-- Can be run against any PostgreSQL database (local, remote, or cloud)

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Optional extension: pg_stat_statements is not available on all Postgres
-- deployments (especially some managed services). Treat it as best-effort so
-- init-db.sql can run successfully even when the extension cannot be installed.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'pg_stat_statements extension not available, skipping';
    END;
END;
$$;

CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For text search improvements
CREATE EXTENSION IF NOT EXISTS btree_gin;  -- For better indexing

-- Set up database configuration
-- Note: Using session-level settings instead of database-level to avoid CURRENT_DATABASE() syntax issues
SET timezone = 'UTC';
SET log_statement = 'all';
SET log_min_duration_statement = 1000;  -- Log slow queries

-- Note: Tables will be created in the public schema by default

-- Create a function to check Shu requirements
CREATE OR REPLACE FUNCTION check_requirements()
RETURNS TABLE(
    requirement TEXT,
    status TEXT,
    details TEXT
) AS $$
BEGIN
    -- Check PostgreSQL version
    RETURN QUERY
    SELECT 
        'PostgreSQL Version'::TEXT,
        CASE 
            WHEN current_setting('server_version_num')::int >= 120000 THEN 'OK'
            ELSE 'WARNING'
        END,
        'Version: ' || version();
    
    -- Check pgvector extension
    RETURN QUERY
    SELECT 
        'pgvector Extension'::TEXT,
        CASE 
            WHEN EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN 'OK'
            ELSE 'ERROR'
        END,
        CASE 
            WHEN EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector') 
            THEN 'pgvector extension is installed'
            ELSE 'pgvector extension is NOT installed - Shu will not work'
        END;
    
    -- Check schema permissions (can create tables in public schema)
    RETURN QUERY
    SELECT
        'Schema Permissions'::TEXT,
        CASE
            WHEN has_schema_privilege('public', 'CREATE') THEN 'OK'
            ELSE 'ERROR'
        END,
        'User: ' || current_user || ' can create tables in public schema';
    
    -- Check available memory
    RETURN QUERY
    SELECT 
        'Memory Configuration'::TEXT,
        'INFO'::TEXT,
        'shared_buffers: ' || current_setting('shared_buffers') || 
        ', work_mem: ' || current_setting('work_mem');
END;
$$ LANGUAGE plpgsql;

-- Create a function to set up Shu-specific database configurations
CREATE OR REPLACE FUNCTION setup_configuration()
RETURNS void AS $$
BEGIN
    -- Configure for vector operations
    PERFORM set_config('max_parallel_workers_per_gather', '4', false);
    
    -- Only set effective_io_concurrency on platforms that support it
    -- Skip on macOS/Darwin which lacks posix_fadvise()
    BEGIN
        PERFORM set_config('effective_io_concurrency', '200', false);
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'Skipping effective_io_concurrency setting (not supported on this platform)';
    END;
    
    PERFORM set_config('random_page_cost', '1.1', false);
    
    -- Configure for text search
    PERFORM set_config('default_text_search_config', 'pg_catalog.english', false);
    
    RAISE NOTICE 'Shu database configuration applied successfully';
END;
$$ LANGUAGE plpgsql;

-- Run the configuration setup
SELECT setup_configuration();

-- Create indexes that Shu will need (these will be created properly by Alembic, but good to have for reference)
-- Note: The actual tables will be created by Alembic migrations

-- Log successful initialization
DO $$
BEGIN
    RAISE NOTICE 'Shu database initialized successfully!';
    RAISE NOTICE 'Run the following to check system requirements:';
    RAISE NOTICE 'SELECT * FROM check_requirements();';
END $$;

-- Show the requirements check
SELECT * FROM check_requirements(); 