-- Carrier delivery performance against promised transit days.

select
    p.carrier_id,
    p.carrier_name,
    p.service_level,
    p.promised_days,
    count(*)                                      as shipment_count,
    count(*) filter (where p.is_delivered)        as delivered_count,
    count(*) filter (where p.met_promise)         as on_time_count,
    count(*) filter (where p.met_promise is false) as late_count,
    count(*) filter (where p.met_promise)::numeric
        / nullif(count(*) filter (where p.is_delivered), 0) as on_time_rate,
    avg(p.transit_days)                           as avg_transit_days,
    percentile_cont(0.95) within group (order by p.transit_days) as p95_transit_days,
    avg(p.days_late) filter (where p.days_late > 0) as avg_days_late_when_late,
    max(p.order_date)                             as last_order_date

from {{ ref('int_shipment_performance') }} p
group by p.carrier_id, p.carrier_name, p.service_level, p.promised_days
