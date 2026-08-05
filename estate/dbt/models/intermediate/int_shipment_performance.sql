-- Shipments measured against the carrier's promise.
--
-- carriers is static reference data read straight from the source; it changes a few times
-- a year and needs no cleaning.

select
    s.shipment_id,
    s.order_id,
    s.carrier_id,
    c.carrier_name,
    c.service_level,
    c.promised_days,
    s.shipped_ts,
    s.delivered_ts,
    s.status,
    s.is_delivered,
    s.transit_days,
    o.order_date,
    o.merchant_id,
    case
        when s.is_delivered then s.transit_days <= c.promised_days
    end                                           as met_promise,
    case
        when s.is_delivered then greatest(s.transit_days - c.promised_days, 0)
    end                                           as days_late

from {{ ref('stg_shipments') }} s
inner join {{ source('raw_pg', 'carriers') }} c
    on s.carrier_id = c.carrier_id
left join {{ ref('stg_orders') }} o
    on s.order_id = o.order_id
