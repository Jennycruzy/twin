-- Merchant Operations :: "On-time delivery rate by carrier"
select carrier_name, service_level, promised_days, shipment_count, on_time_rate, p95_transit_days
from marts.mart_logistics_sla
order by on_time_rate asc nulls first
