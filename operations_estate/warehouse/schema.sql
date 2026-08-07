DROP SCHEMA IF EXISTS ops_erp CASCADE;
DROP SCHEMA IF EXISTS ops_stream CASCADE;
CREATE SCHEMA ops_erp AUTHORIZATION twin;
CREATE SCHEMA ops_stream AUTHORIZATION twin;
CREATE SCHEMA ops_staging AUTHORIZATION twin;
CREATE SCHEMA ops_core AUTHORIZATION twin;
CREATE SCHEMA ops_marts AUTHORIZATION twin;
CREATE SCHEMA ops_features AUTHORIZATION twin;

DO $$
DECLARE s text;
BEGIN
  FOREACH s IN ARRAY ARRAY['ops_erp', 'ops_stream', 'ops_staging', 'ops_core', 'ops_marts', 'ops_features']
  LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO twin_reader, twin_shadow', s);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO twin_reader, twin_shadow', s);
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE twin IN SCHEMA %I GRANT SELECT ON TABLES TO twin_reader, twin_shadow', s);
  END LOOP;
END
$$;

CREATE TABLE ops_erp.facilities (
    facility_id integer PRIMARY KEY, facility_code text NOT NULL, region text NOT NULL,
    capacity_units integer NOT NULL, opened_date date NOT NULL, owner_team text NOT NULL
);
CREATE TABLE ops_erp.carriers (
    carrier_id integer PRIMARY KEY, carrier_name text NOT NULL, service_tier text NOT NULL,
    home_region text NOT NULL
);
CREATE TABLE ops_erp.shipments (
    shipment_id integer PRIMARY KEY, facility_id integer NOT NULL, carrier_id integer NOT NULL,
    route_code text NOT NULL, dispatched_at timestamptz NOT NULL, promised_at timestamptz NOT NULL,
    delivered_at timestamptz, status text NOT NULL, package_count integer NOT NULL, priority text NOT NULL
);
CREATE TABLE ops_erp.inventory_snapshots (
    snapshot_id integer PRIMARY KEY, facility_id integer NOT NULL, snapshot_date date NOT NULL,
    units_on_hand integer NOT NULL, units_reserved integer NOT NULL
);
CREATE TABLE ops_erp.work_orders (
    work_order_id integer PRIMARY KEY, facility_id integer NOT NULL, opened_at timestamptz NOT NULL,
    closed_at timestamptz, issue_code text NOT NULL, severity text NOT NULL, status text NOT NULL
);
CREATE TABLE ops_stream.scan_events (
    event_id text PRIMARY KEY, shipment_id integer NOT NULL, facility_id integer NOT NULL,
    event_type text NOT NULL, event_ts timestamptz NOT NULL, exception_code text
);
CREATE TABLE ops_stream.temperature_readings (
    event_id text PRIMARY KEY, facility_id integer NOT NULL, recorded_at timestamptz NOT NULL,
    celsius numeric(8, 2) NOT NULL, sensor_status text NOT NULL
);
