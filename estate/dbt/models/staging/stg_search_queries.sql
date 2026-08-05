-- On-site search. Zero-result searches are the ones merchandising cares about.

select
    event_id,
    session_id,
    customer_id,
    query_text,
    result_count,
    to_timestamp(event_ts_ms / 1000.0)            as event_ts,
    (result_count = 0)                            as is_zero_result

from {{ source('raw_events', 'search_queries') }}
