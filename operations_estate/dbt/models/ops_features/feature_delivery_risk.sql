{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'risk-science', 'criticality_tier': 'tier1'}) }}

select shipment_id, facility_id, carrier_id,
       case when sla_state = 'late' then 1.0 else 0.0 end as observed_late,
       exception_scan_count, scan_count,
       exception_scan_count::numeric / nullif(scan_count, 0) as exception_scan_ratio
from {{ ref('mart_delivery_sla') }}
