-- Orders with line detail rolled up and every amount converted to USD.
--
-- This is where currency conversion enters the estate. The rate is joined on the order's
-- own date rather than the latest available rate, so restating history does not silently
-- move last quarter's revenue.

with items as (

    select
        order_id,
        count(*)                                  as item_count,
        sum(quantity)                             as unit_count,
        sum(line_amount)                          as items_amount
    from {{ ref('stg_order_items') }}
    group by order_id

)

select
    o.order_id,
    o.customer_id,
    o.merchant_id,
    o.order_ts,
    o.order_date,
    o.status,
    o.channel,
    o.is_completed,
    o.currency_code,
    o.gross_amount,
    o.discount_amount,
    o.shipping_amount,
    o.net_amount,
    coalesce(i.item_count, 0)                     as item_count,
    coalesce(i.unit_count, 0)                     as unit_count,
    coalesce(i.items_amount, 0)                   as items_amount,
    fx.rate                                       as fx_rate_to_usd,
    o.net_amount / fx.rate                        as net_amount_usd,
    o.gross_amount / fx.rate                      as gross_amount_usd,
    o.discount_amount / fx.rate                   as discount_amount_usd

from {{ ref('stg_orders') }} o
left join items i
    on o.order_id = i.order_id
left join {{ ref('stg_fx_rates') }} fx
    on fx.rate_date = o.order_date
   and fx.quote_currency = o.currency_code
   and fx.base_currency = 'USD'
