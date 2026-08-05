-- Session envelopes.
--
-- Roughly 4% of sessions never receive a terminating event, so ended_ts_ms is null and
-- duration is unknown. Those rows are kept rather than dropped: an unterminated session
-- is still a real session, and discarding them would bias every funnel and every
-- device-level risk feature toward users whose app closed cleanly.

select
    session_id,
    customer_id,
    device_id,
    to_timestamp(started_ts_ms / 1000.0)          as started_ts,
    case
        when ended_ts_ms is not null then to_timestamp(ended_ts_ms / 1000.0)
    end                                           as ended_ts,
    case
        when ended_ts_ms is not null then (ended_ts_ms - started_ts_ms) / 1000.0
    end                                           as duration_seconds,
    app_version,
    ip_country,
    (ended_ts_ms is null)                         as is_unterminated,
    (customer_id is null)                         as is_anonymous

from {{ source('raw_events', 'app_sessions') }}
