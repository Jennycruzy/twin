{{ config(meta={'owner': 'lin.nguyen@example.com', 'team': 'risk-science', 'criticality_tier': 'tier1'}) }}

select facility_id, operation_date, facility_code, region, load_ratio, temperature_alerts,
       case when load_ratio >= 0.85 or temperature_alerts > 0 then 1 else 0 end as needs_attention
from {{ ref('mart_capacity_risk') }}
