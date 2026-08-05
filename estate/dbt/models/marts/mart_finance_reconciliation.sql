-- Daily reconciliation between what was ordered and what settled, per processor.
--
-- The unreconciled gap is the number finance chases. It is expressed in USD and as a
-- share of ordered value, because a $40k gap on $50k of orders and on $5m of orders are
-- very different mornings.

with by_processor as (

    select
        attempted_date,
        processor,
        count(*)                                  as attempt_count,
        count(*) filter (where is_settled)        as settled_count,
        sum(amount_usd) filter (where is_settled) as settled_gross_usd,
        sum(net_settled_usd)                      as settled_net_usd,
        sum(refunded_amount_usd)                  as refunded_usd,
        sum(disputed_amount_usd)                  as disputed_usd
    from {{ ref('int_payment_attempts') }}
    group by attempted_date, processor

), ordered_by_date as (

    select
        order_date,
        sum(net_amount_usd) filter (where is_completed) as ordered_net_usd
    from {{ ref('int_orders_enriched') }}
    group by order_date

)

select
    p.attempted_date,
    p.processor,
    p.attempt_count,
    p.settled_count,
    p.settled_gross_usd,
    p.settled_net_usd,
    p.refunded_usd,
    p.disputed_usd,
    o.ordered_net_usd,
    o.ordered_net_usd - p.settled_net_usd         as unreconciled_usd,
    (o.ordered_net_usd - p.settled_net_usd)
        / nullif(o.ordered_net_usd, 0)            as unreconciled_ratio,
    p.settled_count::numeric / nullif(p.attempt_count, 0) as authorisation_rate

from by_processor p
left join ordered_by_date o
    on p.attempted_date = o.order_date
