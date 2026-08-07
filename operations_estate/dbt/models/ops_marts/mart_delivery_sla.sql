{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'transport-analytics', 'criticality_tier': 'tier1'}) }}

select m.shipment_id, m.facility_id, m.carrier_id, c.carrier_name, c.service_tier,
       m.on_time, m.scan_count, m.exception_scan_count,
       case when m.delivered_at is null then 'open' when m.on_time then 'on_time' else 'late' end as sla_state
from {{ ref('int_shipment_milestones') }} m
join {{ ref('stg_carriers') }} c using (carrier_id)
