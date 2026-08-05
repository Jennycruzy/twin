-- Daily revenue in USD, on the order date.
--
-- Reported on gross, net and settled bases because they answer different questions and
-- are routinely confused: orders placed, orders after discount, and money that actually
-- arrived and stayed.

with settled as (

    select
        attempted_date                            as activity_date,
        sum(net_settled_usd)                      as settled_net_usd,
        sum(refunded_amount_usd)                  as refunded_usd,
        sum(disputed_amount_usd)                  as disputed_usd,
        count(*)                                  as payment_attempt_count,
        count(*) filter (where is_settled)        as settled_payment_count
    from {{ ref('int_payment_attempts') }}
    group by attempted_date

), ordered as (

    select
        order_date                                as activity_date,
        count(*)                                  as order_count,
        count(*) filter (where is_completed)      as completed_order_count,
        count(distinct customer_id)               as distinct_customers,
        count(distinct merchant_id)               as distinct_merchants,
        sum(gross_amount_usd)                     as gross_usd,
        sum(net_amount_usd)                       as net_usd,
        sum(discount_amount_usd)                  as discount_usd
    from {{ ref('int_orders_enriched') }}
    group by order_date

)

select
    coalesce(o.activity_date, s.activity_date)    as activity_date,
    coalesce(o.order_count, 0)                    as order_count,
    coalesce(o.completed_order_count, 0)          as completed_order_count,
    coalesce(o.distinct_customers, 0)             as distinct_customers,
    coalesce(o.distinct_merchants, 0)             as distinct_merchants,
    coalesce(o.gross_usd, 0)                      as gross_usd,
    coalesce(o.net_usd, 0)                        as net_usd,
    coalesce(o.discount_usd, 0)                   as discount_usd,
    coalesce(s.settled_net_usd, 0)                as settled_net_usd,
    coalesce(s.refunded_usd, 0)                   as refunded_usd,
    coalesce(s.disputed_usd, 0)                   as disputed_usd,
    coalesce(s.payment_attempt_count, 0)          as payment_attempt_count,
    coalesce(s.settled_payment_count, 0)          as settled_payment_count,
    coalesce(s.settled_payment_count, 0)::numeric
        / nullif(s.payment_attempt_count, 0)      as authorisation_rate

from ordered o
full outer join settled s
    on o.activity_date = s.activity_date
