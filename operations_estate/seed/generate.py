"""Generate a compact, deterministic logistics operations estate.

This is intentionally a different shape from the commerce demo: facilities and routes are
the hubs, event data is append-heavy, and the strongest fan-out starts at a facility rather
than at a customer or order. It shares only the warehouse service with the first estate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import random
import sys
from pathlib import Path

import psycopg


ROOT = Path(__file__).parents[1]
SCHEMA_SQL = ROOT / "warehouse" / "schema.sql"
END = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
START = END - dt.timedelta(days=180)


def stable_id(*parts: object) -> str:
    return hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).hexdigest()


def rows(seed: int) -> list[tuple[str, list[str], list[tuple]]]:
    rng = random.Random(seed)
    facilities = [
        (i, f"FC-{i:03d}", ["north", "south", "east", "west"][i % 4],
         800 + (i * 137) % 1700, dt.date(2018 + i % 6, 1 + i % 9, 1 + i % 20),
         ["inbound", "outbound", "cross-dock"][i % 3])
        for i in range(1, 13)
    ]
    carriers = [
        (i, name, tier, region)
        for i, (name, tier, region) in enumerate(
            [("Northstar Freight", "priority", "north"), ("BlueLine", "standard", "south"),
             ("ParcelWorks", "standard", "east"), ("Orbit Haulage", "economy", "west"),
             ("RapidRelay", "priority", "east")], 1
        )
    ]
    shipments = []
    for shipment_id in range(1, 901):
        facility_id = 1 + (shipment_id * 7) % len(facilities)
        carrier_id = 1 + (shipment_id * 3) % len(carriers)
        dispatched = START + dt.timedelta(hours=(shipment_id * 19) % (180 * 24))
        promised = dispatched + dt.timedelta(days=1 + carrier_id % 4)
        delivered = None
        status = "in_transit"
        if shipment_id % 11 != 0:
            lateness = ((shipment_id * 13) % 48) - 12
            delivered = promised + dt.timedelta(hours=lateness)
            status = "delivered" if lateness <= 24 else "late"
        if shipment_id % 37 == 0:
            status = "exception"
        shipments.append((
            shipment_id, facility_id, carrier_id, f"R-{1 + shipment_id % 24:02d}",
            dispatched, promised, delivered, status, 1 + shipment_id % 8,
            ["standard", "standard", "priority", "fragile"][shipment_id % 4],
        ))

    inventory = []
    inventory_id = 0
    for day in range(0, 181, 3):
        snapshot_date = (START + dt.timedelta(days=day)).date()
        for facility_id, *_ in facilities:
            inventory_id += 1
            capacity = facilities[facility_id - 1][3]
            on_hand = (facility_id * 97 + day * 31) % capacity
            inventory.append((inventory_id, facility_id, snapshot_date, on_hand,
                              (on_hand * (facility_id % 5 + 1)) // 10))

    work_orders = []
    for work_order_id in range(1, 361):
        facility_id = 1 + (work_order_id * 5) % len(facilities)
        opened = START + dt.timedelta(hours=(work_order_id * 29) % (180 * 24))
        closed = None if work_order_id % 9 == 0 else opened + dt.timedelta(hours=4 + work_order_id % 50)
        work_orders.append((work_order_id, facility_id, opened, closed,
                            ["scanner", "conveyor", "dock", "refrigeration"][work_order_id % 4],
                            ["low", "medium", "high", "critical"][work_order_id % 11 // 3],
                            "open" if closed is None else "closed"))

    scans = []
    for index in range(1, 5401):
        shipment_id = 1 + (index * 11) % len(shipments)
        facility_id = shipments[shipment_id - 1][1]
        scan_time = shipments[shipment_id - 1][4] + dt.timedelta(minutes=index % 720)
        scans.append((stable_id("scan", index), shipment_id, facility_id,
                      ["arrived", "loaded", "departed", "exception"][index % 4],
                      scan_time, None if index % 17 else "MISROUTE"))

    temperatures = []
    for index in range(1, 2701):
        facility_id = 1 + (index * 2) % len(facilities)
        recorded = START + dt.timedelta(hours=(index * 7) % (180 * 24))
        temperatures.append((stable_id("temp", index), facility_id, recorded,
                             round(2.0 + rng.random() * 8 + (3 if index % 41 == 0 else 0), 2),
                             "ok" if index % 41 else "alert"))

    return [
        ("ops_erp.facilities", ["facility_id", "facility_code", "region", "capacity_units", "opened_date", "owner_team"], facilities),
        ("ops_erp.carriers", ["carrier_id", "carrier_name", "service_tier", "home_region"], carriers),
        ("ops_erp.shipments", ["shipment_id", "facility_id", "carrier_id", "route_code", "dispatched_at", "promised_at", "delivered_at", "status", "package_count", "priority"], shipments),
        ("ops_erp.inventory_snapshots", ["snapshot_id", "facility_id", "snapshot_date", "units_on_hand", "units_reserved"], inventory),
        ("ops_erp.work_orders", ["work_order_id", "facility_id", "opened_at", "closed_at", "issue_code", "severity", "status"], work_orders),
        ("ops_stream.scan_events", ["event_id", "shipment_id", "facility_id", "event_type", "event_ts", "exception_code"], scans),
        ("ops_stream.temperature_readings", ["event_id", "facility_id", "recorded_at", "celsius", "sensor_status"], temperatures),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=int(os.environ.get("TWIN_OPERATIONS_SEED", "4417")))
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args(argv)
    dsn = args.dsn or (
        f"host={os.environ.get('WAREHOUSE_HOST', 'warehouse')} "
        f"port={os.environ.get('WAREHOUSE_PORT', '5432')} "
        f"dbname={os.environ.get('WAREHOUSE_DB', 'warehouse')} "
        f"user={os.environ.get('WAREHOUSE_USER', 'twin')} "
        f"password={os.environ.get('WAREHOUSE_PASSWORD', 'twin')}"
    )
    plan = rows(args.seed)
    print(f"seeding operations estate  seed={args.seed}  tables={len(plan)}")
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute(SCHEMA_SQL.read_text())
        total = 0
        for table, columns, values in plan:
            schema, name = table.split(".")
            quoted = ", ".join('"' + col + '"' for col in columns)
            with conn.cursor() as cur:
                with cur.copy(f"COPY {schema}.{name} ({quoted}) FROM STDIN") as copy:
                    for value in values:
                        copy.write_row(value)
            total += len(values)
            print(f"  {table:<36} {len(values):>7,} rows")
        for schema in ("ops_erp", "ops_stream"):
            conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO twin_reader, twin_shadow")
            conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO twin_reader, twin_shadow")
        conn.commit()
    print(f"seeded {total:,} rows across {len(plan)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
