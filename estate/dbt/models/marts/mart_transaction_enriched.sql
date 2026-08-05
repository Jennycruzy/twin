-- Payment-attempt grain, enriched with the order it paid for and the device behaviour of
-- the customer behind it.
--
-- Device signal is joined at customer grain rather than session grain: payments carry no
-- session identifier, and inventing a time-proximity join between the two would produce
-- a number that looks precise and is not.

with device_profile as (

    select
        customer_id,
        count(distinct device_id)                 as distinct_devices,
        count(distinct session_id)                as session_count,
        sum(failed_login_count)                   as failed_login_count,
        sum(failed_mfa_count)                     as failed_mfa_count,
        min(trust_score)                          as min_device_trust,
        avg(trust_score)                          as avg_device_trust,
        bool_or(is_emulator)                      as any_emulator,
        max(distinct_countries)                   as max_countries_per_device
    from {{ ref('int_session_device') }}
    where customer_id is not null
    group by customer_id

)

select
    p.payment_id,
    p.order_id,
    p.attempted_ts,
    p.attempted_date,
    p.status,
    p.processor,
    p.decline_reason,
    p.is_settled,
    p.settle_latency_seconds,
    p.currency_code,
    p.amount_usd,
    p.net_settled_usd,
    p.refunded_amount_usd,
    p.disputed_amount_usd,
    p.has_fraud_dispute,
    p.has_open_dispute,
    o.customer_id,
    o.merchant_id,
    o.channel,
    o.item_count,
    o.unit_count,
    o.net_amount_usd                              as order_net_amount_usd,
    coalesce(d.distinct_devices, 0)               as distinct_devices,
    coalesce(d.session_count, 0)                  as session_count,
    coalesce(d.failed_login_count, 0)             as failed_login_count,
    coalesce(d.failed_mfa_count, 0)               as failed_mfa_count,
    d.min_device_trust,
    d.avg_device_trust,
    coalesce(d.any_emulator, false)               as any_emulator,
    coalesce(d.max_countries_per_device, 0)       as max_countries_per_device

from {{ ref('int_payment_attempts') }} p
left join {{ ref('int_orders_enriched') }} o
    on p.order_id = o.order_id
left join device_profile d
    on o.customer_id = d.customer_id
