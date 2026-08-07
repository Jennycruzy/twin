{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'transport-analytics', 'criticality_tier': 'tier1'}) }}

select e.event_id, e.shipment_id, e.facility_id, e.event_ts, e.exception_code,
       s.route_code, s.priority
from {{ ref('stg_scan_events') }} e
join {{ ref('stg_shipments') }} s using (shipment_id)
where e.is_exception
