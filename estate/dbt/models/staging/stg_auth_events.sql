-- Authentication attempts. Failed logins and MFA challenges are the raw signal behind
-- the customer risk features, so the failure flag is resolved once here.

select
    event_id,
    customer_id,
    device_id,
    event_type,
    succeeded,
    ip_country,
    to_timestamp(event_ts_ms / 1000.0)            as event_ts,
    (not succeeded and event_type = 'login')      as is_failed_login,
    (not succeeded and event_type = 'mfa_challenge') as is_failed_mfa

from {{ source('raw_events', 'auth_events') }}
