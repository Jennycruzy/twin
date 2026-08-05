-- Estate schemas.
--
-- Two source systems land in separate raw schemas because they have genuinely different
-- freshness characteristics, and Twin's propagation model is driven by refresh cadence:
--
--   raw_pg      a nightly snapshot of a transactional PostgreSQL replica. Batch, slow,
--               strongly consistent, one refresh per day.
--   raw_events  a continuously landing event stream. Frequent, late-arriving, gappy.
--
-- The dbt project builds staging -> intermediate -> marts -> ml on top of both.

CREATE SCHEMA raw_pg     AUTHORIZATION twin;
CREATE SCHEMA raw_events AUTHORIZATION twin;
CREATE SCHEMA staging      AUTHORIZATION twin;
CREATE SCHEMA intermediate AUTHORIZATION twin;
CREATE SCHEMA marts        AUTHORIZATION twin;
CREATE SCHEMA ml           AUTHORIZATION twin;

-- Read access for the two Twin roles, including on tables dbt has not created yet.
DO $$
DECLARE
  s text;
BEGIN
  FOREACH s IN ARRAY ARRAY['raw_pg', 'raw_events', 'staging', 'intermediate', 'marts', 'ml']
  LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO twin_reader, twin_shadow', s);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO twin_reader, twin_shadow', s);
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE twin IN SCHEMA %I GRANT SELECT ON TABLES TO twin_reader, twin_shadow',
      s
    );
  END LOOP;
END
$$;
