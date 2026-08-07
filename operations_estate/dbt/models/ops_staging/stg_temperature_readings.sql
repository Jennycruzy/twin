{{ config(meta={'owner': 'diego.mora@example.com', 'team': 'facilities', 'criticality_tier': 'tier2'}) }}

select event_id, facility_id, recorded_at, celsius, sensor_status,
       sensor_status = 'alert' as is_alert
from {{ source('ops_stream', 'temperature_readings') }}
