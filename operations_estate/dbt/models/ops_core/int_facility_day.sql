{{ config(meta={'owner': 'lin.nguyen@example.com', 'team': 'network-operations', 'criticality_tier': 'tier1'}) }}

select
    facility_id, date_trunc('day', dispatched_at)::date as operation_date,
    count(*) as shipments_dispatched, sum(package_count) as packages_dispatched,
    count(*) filter (where priority in ('priority', 'fragile')) as priority_shipments
from {{ ref('stg_shipments') }}
group by 1, 2
