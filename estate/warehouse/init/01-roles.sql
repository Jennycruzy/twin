-- Warehouse role model.
--
-- Twin runs as three distinct roles with deliberately different power. The separation is
-- what makes Stage 4 (shadow execution of real faults) safe by construction rather than
-- by convention. See docs/SAFETY.md.
--
--   twin          owner. Seeds raw data and builds the estate with dbt. Never used by
--                 the Twin pipeline itself.
--   twin_reader   Stage 1 and Stage 3. SELECT on the estate, and no write privilege
--                 anywhere in the database.
--   twin_shadow   Stage 4. Creates its own twin_shadow_* schemas, clones the affected
--                 slice into them, and executes faults there.
--
-- The guarantee that matters: in PostgreSQL, DROP TABLE and ALTER TABLE require object
-- ownership, and ownership cannot be assumed at will. twin_shadow owns no object in the
-- estate — every estate object is owned by twin — so twin_shadow is structurally
-- incapable of dropping or altering a real table, whatever statement it is handed. It
-- can only destroy what it created, which is only ever inside a schema it created.
--
-- This is the database-level half of the guard. The other half is enforced in Twin's
-- execution boundary, which refuses any destructive statement naming an object outside a
-- twin_shadow_ prefix, so a misconfigured role cannot silently widen the blast radius.
--
-- Passwords here are fixed because this stack is local-only and disposable. The warehouse
-- port is published to the host for inspection with psql; nothing in it is real data.

CREATE ROLE twin_reader WITH LOGIN PASSWORD 'twin_reader';
CREATE ROLE twin_shadow WITH LOGIN PASSWORD 'twin_shadow';

-- By default PUBLIC may create objects in the public schema, which would give both roles
-- a place to write outside our intent.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE warehouse FROM PUBLIC;

GRANT CONNECT ON DATABASE warehouse TO twin_reader, twin_shadow;

-- twin_shadow needs CREATE on the database so it can make its own shadow schemas. This
-- is the widest privilege it holds, and it does not imply any power over existing
-- objects: creating a schema grants nothing over schemas it did not create.
GRANT CREATE ON DATABASE warehouse TO twin_shadow;
