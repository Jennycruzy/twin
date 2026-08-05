-- Per-customer order history rolled to a lifetime view.
--
-- Customers who have never ordered are retained with zeroed measures. They are the
-- majority of the base and dropping them would make every "average customer" figure in
-- the estate describe only buyers.

with order_rollup as (

    select
        customer_id,
        count(*)                                  as order_count,
        count(*) filter (where is_completed)      as completed_order_count,
        sum(net_amount_usd) filter (where is_completed) as lifetime_net_usd,
        min(order_ts)                             as first_order_ts,
        max(order_ts)                             as last_order_ts,
        count(distinct merchant_id)               as distinct_merchants
    from {{ ref('int_orders_enriched') }}
    group by customer_id

)

select
    c.customer_id,
    c.email,
    c.country_code,
    c.signup_ts,
    c.account_status,
    c.lifetime_tier,
    c.is_active,
    coalesce(o.order_count, 0)                    as order_count,
    coalesce(o.completed_order_count, 0)          as completed_order_count,
    coalesce(o.lifetime_net_usd, 0)               as lifetime_net_usd,
    coalesce(o.distinct_merchants, 0)             as distinct_merchants,
    o.first_order_ts,
    o.last_order_ts,
    case
        when o.last_order_ts is not null
            then extract(epoch from (date '{{ var("estate_end_date") }}' - o.last_order_ts)) / 86400.0
    end                                           as days_since_last_order,
    (coalesce(o.order_count, 0) = 0)              as is_never_ordered

from {{ ref('stg_customers') }} c
left join order_rollup o
    on c.customer_id = o.customer_id
