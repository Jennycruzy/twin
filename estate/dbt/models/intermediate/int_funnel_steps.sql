-- Session-grain funnel: what each session reached, and where it stopped.
--
-- Built as a per-session rollup rather than an event stream because every consumer of it
-- asks session-level questions, and doing the collapse once here keeps the marts cheap.

with views as (

    select session_id, count(*) as page_view_count, min(event_ts) as first_view_ts
    from {{ ref('stg_page_views') }}
    group by session_id

), product_views as (

    select session_id, count(*) as product_view_count, count(distinct product_id) as distinct_products_viewed
    from {{ ref('stg_product_views') }}
    group by session_id

), cart as (

    select session_id, count(*) as add_to_cart_count, sum(quantity) as cart_units
    from {{ source('raw_events', 'add_to_cart') }}
    group by session_id

), checkout as (

    select
        session_id,
        max(step_index)                           as furthest_step_index,
        bool_or(abandoned)                        as any_abandoned,
        count(*)                                  as checkout_step_count
    from {{ source('raw_events', 'checkout_steps') }}
    group by session_id

)

select
    s.session_id,
    s.customer_id,
    s.started_ts,
    s.started_ts::date                            as session_date,
    s.is_anonymous,
    coalesce(v.page_view_count, 0)                as page_view_count,
    coalesce(pv.product_view_count, 0)            as product_view_count,
    coalesce(pv.distinct_products_viewed, 0)      as distinct_products_viewed,
    coalesce(c.add_to_cart_count, 0)              as add_to_cart_count,
    coalesce(c.cart_units, 0)                     as cart_units,
    coalesce(ck.checkout_step_count, 0)           as checkout_step_count,
    ck.furthest_step_index,
    coalesce(ck.any_abandoned, false)             as any_abandoned,
    (coalesce(pv.product_view_count, 0) > 0)      as reached_product,
    (coalesce(c.add_to_cart_count, 0) > 0)        as reached_cart,
    (ck.furthest_step_index is not null)          as reached_checkout,
    (ck.furthest_step_index = 3)                  as reached_confirm

from {{ ref('stg_app_sessions') }} s
left join views v          on s.session_id = v.session_id
left join product_views pv on s.session_id = pv.session_id
left join cart c           on s.session_id = c.session_id
left join checkout ck      on s.session_id = ck.session_id
