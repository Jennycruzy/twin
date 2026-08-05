-- Page views with the producer's epoch milliseconds converted to a real timestamp.
--
-- The event stream sends utm_source as an empty-ish free-text field; normalising it to
-- 'direct' here is the difference between a funnel report that segments and one that has
-- a large unlabelled bucket.

select
    event_id,
    session_id,
    customer_id,
    to_timestamp(event_ts_ms / 1000.0)            as event_ts,
    path,
    referrer,
    coalesce(nullif(utm_source, ''), 'direct')    as utm_source,
    device_type

from {{ source('raw_events', 'page_views') }}
