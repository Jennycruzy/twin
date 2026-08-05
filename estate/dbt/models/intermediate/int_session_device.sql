-- Sessions joined to the device that produced them, with authentication outcomes.
--
-- This is the join that makes device-level risk possible: a session alone says what
-- happened, a device alone says who it happened on, and the fraud features need both.

with auth_rollup as (

    select
        device_id,
        count(*)                                  as auth_event_count,
        count(*) filter (where is_failed_login)   as failed_login_count,
        count(*) filter (where is_failed_mfa)     as failed_mfa_count,
        count(distinct customer_id)               as distinct_customers_on_device,
        count(distinct ip_country)                as distinct_countries
    from {{ ref('stg_auth_events') }}
    group by device_id

)

select
    s.session_id,
    s.customer_id,
    s.device_id,
    s.started_ts,
    s.ended_ts,
    s.duration_seconds,
    s.app_version,
    s.ip_country,
    s.is_unterminated,
    s.is_anonymous,
    d.os_family,
    d.is_emulator,
    d.trust_score,
    d.is_low_trust,
    d.first_seen_ts                               as device_first_seen_ts,
    coalesce(a.auth_event_count, 0)               as auth_event_count,
    coalesce(a.failed_login_count, 0)             as failed_login_count,
    coalesce(a.failed_mfa_count, 0)               as failed_mfa_count,
    coalesce(a.distinct_customers_on_device, 0)   as distinct_customers_on_device,
    coalesce(a.distinct_countries, 0)             as distinct_countries

from {{ ref('stg_app_sessions') }} s
left join {{ ref('stg_device_fingerprints') }} d
    on s.device_id = d.device_id
left join auth_rollup a
    on s.device_id = a.device_id
