{{ config(meta={'owner': 'lin.nguyen@example.com', 'team': 'network-operations', 'criticality_tier': 'tier2'}) }}

select snapshot_id, facility_id, snapshot_date, units_on_hand, units_reserved,
       units_on_hand + units_reserved as units_committed
from {{ source('ops_erp', 'inventory_snapshots') }}
