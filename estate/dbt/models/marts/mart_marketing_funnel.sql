-- Daily acquisition funnel at session grain, with search behaviour attached.

with search as (

    select
        session_id,
        count(*)                                  as search_count,
        count(*) filter (where is_zero_result)    as zero_result_count
    from {{ ref('stg_search_queries') }}
    group by session_id

)

select
    f.session_date,
    count(*)                                      as session_count,
    count(*) filter (where f.is_anonymous)        as anonymous_session_count,
    count(*) filter (where f.reached_product)     as reached_product_count,
    count(*) filter (where f.reached_cart)        as reached_cart_count,
    count(*) filter (where f.reached_checkout)    as reached_checkout_count,
    count(*) filter (where f.reached_confirm)     as reached_confirm_count,
    count(*) filter (where f.any_abandoned)       as abandoned_count,
    sum(f.page_view_count)                        as page_view_count,
    sum(f.product_view_count)                     as product_view_count,
    sum(coalesce(s.search_count, 0))              as search_count,
    sum(coalesce(s.zero_result_count, 0))         as zero_result_count,
    count(*) filter (where f.reached_cart)::numeric
        / nullif(count(*) filter (where f.reached_product), 0) as product_to_cart_rate,
    count(*) filter (where f.reached_confirm)::numeric
        / nullif(count(*) filter (where f.reached_cart), 0)    as cart_to_confirm_rate

from {{ ref('int_funnel_steps') }} f
left join search s
    on f.session_id = s.session_id
group by f.session_date
