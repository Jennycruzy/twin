{{ config(meta={'owner': 'diego.mora@example.com', 'team': 'facilities', 'criticality_tier': 'tier2'}) }}

select facility_id, date_trunc('day', recorded_at)::date as condition_date,
       avg(celsius) as mean_temperature,
       count(*) filter (where is_alert) as temperature_alerts
from {{ ref('stg_temperature_readings') }}
group by 1, 2
