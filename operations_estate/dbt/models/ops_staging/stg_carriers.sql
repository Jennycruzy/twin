{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'transport-analytics', 'criticality_tier': 'tier3'}) }}

select carrier_id, carrier_name, service_tier, home_region
from {{ source('ops_erp', 'carriers') }}
