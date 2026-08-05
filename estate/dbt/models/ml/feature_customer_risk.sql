-- Customer-grain risk features for the fraud scorer.
--
-- Freshness matters here more than anywhere else in the estate: these features are read
-- at scoring time by a model serving live traffic, so a stale row is not a stale report,
-- it is a wrong decision on a real transaction.

with device_signal as (

    select
        customer_id,
        count(distinct device_id)                 as device_count,
        count(distinct session_id)                as session_count,
        count(*) filter (where is_low_trust)      as low_trust_session_count,
        sum(failed_login_count)                   as failed_login_count,
        sum(failed_mfa_count)                     as failed_mfa_count,
        max(distinct_customers_on_device)         as max_customers_sharing_device,
        max(distinct_countries)                   as max_countries_per_device,
        min(trust_score)                          as min_device_trust,
        avg(coalesce(duration_seconds, 0))        as avg_session_seconds,
        count(*) filter (where is_unterminated)   as unterminated_session_count
    from {{ ref('int_session_device') }}
    where customer_id is not null
    group by customer_id

)

select
    l.customer_id,
    l.country_code,
    l.order_count,
    l.lifetime_net_usd,
    l.days_since_last_order,
    l.is_never_ordered,
    extract(epoch from (date '{{ var("estate_end_date") }}' - l.signup_ts)) / 86400.0 as account_age_days,
    coalesce(d.device_count, 0)                   as device_count,
    coalesce(d.session_count, 0)                  as session_count,
    coalesce(d.low_trust_session_count, 0)        as low_trust_session_count,
    coalesce(d.failed_login_count, 0)             as failed_login_count,
    coalesce(d.failed_mfa_count, 0)               as failed_mfa_count,
    coalesce(d.max_customers_sharing_device, 0)   as max_customers_sharing_device,
    coalesce(d.max_countries_per_device, 0)       as max_countries_per_device,
    d.min_device_trust,
    d.avg_session_seconds,
    coalesce(d.unterminated_session_count, 0)     as unterminated_session_count,
    coalesce(d.failed_login_count, 0)::numeric
        / nullif(d.session_count, 0)              as failed_login_rate,
    coalesce(d.device_count, 0)::numeric
        / nullif(l.order_count, 0)                as devices_per_order

from {{ ref('int_customer_lifetime') }} l
left join device_signal d
    on l.customer_id = d.customer_id
