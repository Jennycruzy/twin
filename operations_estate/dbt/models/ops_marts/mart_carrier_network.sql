{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'transport-analytics', 'criticality_tier': 'tier2'}) }}

select c.carrier_id, c.carrier_name, c.service_tier, c.home_region,
       r.shipment_count, r.on_time_shipments, r.on_time_rate, r.mean_transit_hours
from {{ ref('stg_carriers') }} c
join {{ ref('int_carrier_reliability') }} r using (carrier_id)
