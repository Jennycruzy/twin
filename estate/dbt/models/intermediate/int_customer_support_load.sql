-- Support contact volume per customer, with resolution time and satisfaction.

with tickets as (

    select
        t.ticket_id,
        t.customer_id,
        t.order_id,
        t.opened_ts,
        t.closed_ts,
        t.category,
        t.priority,
        t.csat_score,
        extract(epoch from (t.closed_ts - t.opened_ts)) / 3600.0 as resolution_hours
    from {{ source('raw_pg', 'support_tickets') }} t

)

select
    c.customer_id,
    c.country_code,
    c.lifetime_tier,
    count(t.ticket_id)                            as ticket_count,
    count(t.ticket_id) filter (where t.closed_ts is null) as open_ticket_count,
    count(t.ticket_id) filter (where t.priority in ('high', 'urgent')) as escalated_ticket_count,
    count(distinct t.order_id)                    as orders_with_tickets,
    avg(t.resolution_hours)                       as avg_resolution_hours,
    avg(t.csat_score)                             as avg_csat_score,
    max(t.opened_ts)                              as last_ticket_ts

from {{ ref('stg_customers') }} c
left join tickets t
    on c.customer_id = t.customer_id
group by c.customer_id, c.country_code, c.lifetime_tier
