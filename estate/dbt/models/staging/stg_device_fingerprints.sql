-- Device identity and trust score.

select
    device_id,
    to_timestamp(first_seen_ms / 1000.0)          as first_seen_ts,
    to_timestamp(last_seen_ms / 1000.0)           as last_seen_ts,
    os_family,
    is_emulator,
    trust_score,
    (trust_score < 0.4 or is_emulator)            as is_low_trust

from {{ source('raw_events', 'device_fingerprints') }}
