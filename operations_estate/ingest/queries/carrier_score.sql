select carrier_name, service_tier, on_time_rate, mean_transit_hours
from ops_marts.mart_carrier_network
order by on_time_rate desc
