{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'transport-analytics', 'criticality_tier': 'tier1'}) }}

select
    shipment_id, facility_id, carrier_id, route_code, dispatched_at, promised_at, delivered_at,
    status, package_count, priority,
    extract(epoch from (delivered_at - dispatched_at)) / 3600.0 as transit_hours,
    delivered_at is not null and delivered_at <= promised_at as on_time
from {{ source('ops_erp', 'shipments') }}
