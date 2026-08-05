-- Shipments with transit time resolved for delivered parcels.

select
    shipment_id,
    order_id,
    carrier_id,
    shipped_ts,
    delivered_ts,
    status,
    tracking_ref,
    (status = 'delivered')                        as is_delivered,
    extract(epoch from (delivered_ts - shipped_ts)) / 86400.0 as transit_days

from {{ source('raw_pg', 'shipments') }}
