{{ config(meta={'owner': 'sofia.kim@example.com', 'team': 'transport-analytics', 'criticality_tier': 'tier1'}) }}

select event_id, shipment_id, facility_id, event_type, event_ts, exception_code,
       exception_code is not null as is_exception
from {{ source('ops_stream', 'scan_events') }}
