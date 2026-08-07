{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'transport-analytics', 'criticality_tier': 'tier1'}) }}

select
    s.shipment_id, s.facility_id, s.carrier_id, s.promised_at, s.delivered_at, s.on_time,
    count(e.event_id) as scan_count,
    count(*) filter (where e.is_exception) as exception_scan_count,
    max(e.event_ts) as last_scan_at
from {{ ref('stg_shipments') }} s
left join {{ ref('stg_scan_events') }} e on e.shipment_id = s.shipment_id
group by 1, 2, 3, 4, 5, 6
