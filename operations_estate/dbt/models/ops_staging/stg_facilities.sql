{{ config(meta={'owner': 'lin.nguyen@example.com', 'team': 'network-operations', 'criticality_tier': 'tier1'}) }}

select facility_id, facility_code, region, capacity_units, opened_date, owner_team
from {{ source('ops_erp', 'facilities') }}
