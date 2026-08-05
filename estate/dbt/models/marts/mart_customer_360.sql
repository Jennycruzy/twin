-- One row per customer: value, support burden, and subscription state.

with subs as (

    select
        customer_id,
        count(*)                                  as subscription_count,
        count(*) filter (where is_active)         as active_subscription_count,
        sum(normalised_mrr) filter (where is_active) as active_mrr,
        min(started_ts)                           as first_subscription_ts
    from {{ ref('stg_subscriptions') }}
    group by customer_id

)

select
    l.customer_id,
    l.email,
    l.country_code,
    l.signup_ts,
    l.account_status,
    l.lifetime_tier,
    l.is_active,
    l.order_count,
    l.completed_order_count,
    l.lifetime_net_usd,
    l.distinct_merchants,
    l.first_order_ts,
    l.last_order_ts,
    l.days_since_last_order,
    l.is_never_ordered,
    coalesce(s.ticket_count, 0)                   as ticket_count,
    coalesce(s.open_ticket_count, 0)              as open_ticket_count,
    coalesce(s.escalated_ticket_count, 0)         as escalated_ticket_count,
    s.avg_resolution_hours,
    s.avg_csat_score,
    coalesce(b.subscription_count, 0)             as subscription_count,
    coalesce(b.active_subscription_count, 0)      as active_subscription_count,
    coalesce(b.active_mrr, 0)                     as active_mrr,
    (coalesce(b.active_subscription_count, 0) > 0) as is_subscriber

from {{ ref('int_customer_lifetime') }} l
left join {{ ref('int_customer_support_load') }} s
    on l.customer_id = s.customer_id
left join subs b
    on l.customer_id = b.customer_id
