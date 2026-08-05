-- Merchant-grain risk features. Dispute and refund intensity relative to volume is the
-- signal; absolute counts alone would just rank merchants by size.

with disputes as (

    select
        o.merchant_id,
        count(*) filter (where p.has_fraud_dispute) as fraud_dispute_count,
        count(*) filter (where p.dispute_count > 0) as dispute_count,
        count(*) filter (where p.refund_count > 0)  as refunded_payment_count,
        count(*) filter (where not p.is_settled)    as declined_payment_count,
        count(*)                                    as payment_count,
        sum(p.disputed_amount_usd)                  as disputed_usd,
        sum(p.refunded_amount_usd)                  as refunded_usd
    from {{ ref('int_payment_attempts') }} p
    inner join {{ ref('int_orders_enriched') }} o
        on p.order_id = o.order_id
    group by o.merchant_id

), volume as (

    select
        merchant_id,
        merchant_name,
        risk_band,
        is_suspended,
        count(distinct order_date)                as active_days,
        sum(order_count)                          as order_count,
        sum(distinct_customers)                   as customer_touches,
        sum(net_amount_usd)                       as net_amount_usd
    from {{ ref('int_merchant_daily') }}
    group by merchant_id, merchant_name, risk_band, is_suspended

)

select
    v.merchant_id,
    v.merchant_name,
    v.risk_band,
    v.is_suspended,
    v.active_days,
    v.order_count,
    v.customer_touches,
    v.net_amount_usd,
    coalesce(d.payment_count, 0)                  as payment_count,
    coalesce(d.declined_payment_count, 0)         as declined_payment_count,
    coalesce(d.dispute_count, 0)                  as dispute_count,
    coalesce(d.fraud_dispute_count, 0)            as fraud_dispute_count,
    coalesce(d.refunded_payment_count, 0)         as refunded_payment_count,
    coalesce(d.disputed_usd, 0)                   as disputed_usd,
    coalesce(d.refunded_usd, 0)                   as refunded_usd,
    coalesce(d.declined_payment_count, 0)::numeric
        / nullif(d.payment_count, 0)              as decline_rate,
    coalesce(d.dispute_count, 0)::numeric
        / nullif(d.payment_count, 0)              as dispute_rate,
    coalesce(d.disputed_usd, 0)
        / nullif(v.net_amount_usd, 0)             as disputed_value_share

from volume v
left join disputes d
    on v.merchant_id = d.merchant_id
