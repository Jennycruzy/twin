-- Product detail views. Dwell time is null when the producer never saw the page close.

select
    event_id,
    session_id,
    customer_id,
    product_id,
    to_timestamp(event_ts_ms / 1000.0)            as event_ts,
    dwell_ms,
    (dwell_ms >= 30000)                           as is_engaged_view

from {{ source('raw_events', 'product_views') }}
