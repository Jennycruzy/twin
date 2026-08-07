{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'transport-analytics', 'criticality_tier': 'tier2'}) }}

select carrier_id, count(*) as shipment_count,
       count(*) filter (where on_time) as on_time_shipments,
       round(count(*) filter (where on_time)::numeric / nullif(count(*), 0), 4) as on_time_rate,
       avg(transit_hours) as mean_transit_hours
from {{ ref('stg_shipments') }}
group by 1
