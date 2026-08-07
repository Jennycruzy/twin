select facility_code, region, operation_date, load_ratio, temperature_alerts
from ops_marts.mart_capacity_risk
where load_ratio >= 0.55 or temperature_alerts > 0
order by operation_date desc, load_ratio desc
limit 100
