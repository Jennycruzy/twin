{{ config(meta={'owner': 'lin.nguyen@example.com', 'team': 'network-operations', 'criticality_tier': 'tier1'}) }}

select d.facility_id, d.operation_date, f.facility_code, f.region, f.capacity_units,
       d.packages_dispatched, i.units_committed,
       round((d.packages_dispatched + i.units_committed)::numeric / nullif(f.capacity_units, 0), 4) as load_ratio,
       coalesce(c.temperature_alerts, 0) as temperature_alerts
from {{ ref('int_facility_day') }} d
join {{ ref('stg_facilities') }} f using (facility_id)
left join {{ ref('stg_inventory_snapshots') }} i
  on i.facility_id = d.facility_id and i.snapshot_date = d.operation_date
left join {{ ref('int_facility_conditions') }} c
  on c.facility_id = d.facility_id and c.condition_date = d.operation_date
