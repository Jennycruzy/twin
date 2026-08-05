-- Promotion redemptions attributed to the orders they discounted.
--
-- raw_pg.promotion_redemptions is read directly rather than through a staging model: it
-- arrives already clean, and a pass-through staging layer would add a hop without adding
-- a transformation.

select
    p.promotion_id,
    p.promo_code,
    p.discount_type,
    p.discount_value,
    p.budget_amount,
    p.starts_ts,
    p.ends_ts,
    p.window_days,
    count(r.redemption_id)                        as redemption_count,
    count(distinct r.customer_id)                 as distinct_redeemers,
    sum(r.discount_applied)                       as discount_applied_local,
    sum(o.discount_amount_usd)                    as discount_applied_usd,
    sum(o.net_amount_usd)                         as attributed_net_usd,
    sum(o.discount_amount_usd) / nullif(p.budget_amount, 0) as budget_consumed_ratio

from {{ ref('stg_promotions') }} p
left join {{ source('raw_pg', 'promotion_redemptions') }} r
    on p.promotion_id = r.promotion_id
left join {{ ref('int_orders_enriched') }} o
    on r.order_id = o.order_id
group by
    p.promotion_id,
    p.promo_code,
    p.discount_type,
    p.discount_value,
    p.budget_amount,
    p.starts_ts,
    p.ends_ts,
    p.window_days
