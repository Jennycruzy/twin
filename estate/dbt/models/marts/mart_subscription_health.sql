-- Subscription book by plan, with MRR converted to USD.

select
    s.plan_code,
    s.billing_interval,
    count(*)                                      as subscription_count,
    count(*) filter (where s.is_active)           as active_count,
    count(*) filter (where not s.is_active)       as cancelled_count,
    count(*) filter (where not s.is_active)::numeric
        / nullif(count(*), 0)                     as churn_rate,
    sum(s.normalised_mrr / fx.rate)               as mrr_usd,
    sum(s.normalised_mrr / fx.rate) filter (where s.is_active) as active_mrr_usd,
    avg(s.normalised_mrr / fx.rate)               as avg_mrr_usd,
    avg(l.lifetime_net_usd)                       as avg_subscriber_lifetime_usd,
    count(distinct s.customer_id)                 as distinct_subscribers

from {{ ref('stg_subscriptions') }} s
left join {{ ref('stg_fx_rates') }} fx
    on fx.rate_date = s.started_ts::date
   and fx.quote_currency = s.currency_code
   and fx.base_currency = 'USD'
left join {{ ref('int_customer_lifetime') }} l
    on s.customer_id = l.customer_id
group by s.plan_code, s.billing_interval
