-- Merchant activity per day. The grain the merchant scorecard and merchant risk features
-- both build on.

select
    o.merchant_id,
    o.order_date,
    m.merchant_name,
    m.country_code,
    m.risk_band,
    m.settlement_currency,
    m.is_suspended,
    count(*)                                      as order_count,
    count(*) filter (where o.is_completed)        as completed_order_count,
    count(distinct o.customer_id)                 as distinct_customers,
    sum(o.net_amount_usd)                         as net_amount_usd,
    sum(o.net_amount_usd) filter (where o.is_completed) as completed_net_amount_usd,
    sum(o.discount_amount_usd)                    as discount_amount_usd,
    sum(o.unit_count)                             as unit_count

from {{ ref('int_orders_enriched') }} o
inner join {{ ref('stg_merchants') }} m
    on o.merchant_id = m.merchant_id
group by
    o.merchant_id,
    o.order_date,
    m.merchant_name,
    m.country_code,
    m.risk_band,
    m.settlement_currency,
    m.is_suspended
