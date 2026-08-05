-- Merchant-level performance across revenue and fulfilment.

with revenue as (

    select
        merchant_id,
        merchant_name,
        country_code,
        risk_band,
        is_suspended,
        count(distinct order_date)                as active_days,
        sum(order_count)                          as order_count,
        sum(completed_order_count)                as completed_order_count,
        sum(net_amount_usd)                       as net_amount_usd,
        sum(completed_net_amount_usd)             as completed_net_amount_usd,
        sum(discount_amount_usd)                  as discount_amount_usd,
        max(order_date)                           as last_order_date
    from {{ ref('int_merchant_daily') }}
    group by merchant_id, merchant_name, country_code, risk_band, is_suspended

), fulfilment as (

    select
        merchant_id,
        count(*)                                  as shipment_count,
        count(*) filter (where met_promise)       as on_time_count,
        avg(transit_days)                         as avg_transit_days,
        avg(days_late)                            as avg_days_late
    from {{ ref('int_shipment_performance') }}
    where merchant_id is not null
    group by merchant_id

)

select
    r.merchant_id,
    r.merchant_name,
    r.country_code,
    r.risk_band,
    r.is_suspended,
    r.active_days,
    r.order_count,
    r.completed_order_count,
    r.net_amount_usd,
    r.completed_net_amount_usd,
    r.discount_amount_usd,
    r.last_order_date,
    coalesce(f.shipment_count, 0)                 as shipment_count,
    coalesce(f.on_time_count, 0)                  as on_time_count,
    f.on_time_count::numeric / nullif(f.shipment_count, 0) as on_time_rate,
    f.avg_transit_days,
    f.avg_days_late,
    r.discount_amount_usd / nullif(r.net_amount_usd, 0) as discount_intensity

from revenue r
left join fulfilment f
    on r.merchant_id = f.merchant_id
