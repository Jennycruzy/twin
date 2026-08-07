select facility_id, carrier_id, sla_state, count(*) as shipments
from ops_marts.mart_delivery_sla
group by facility_id, carrier_id, sla_state
order by shipments desc
