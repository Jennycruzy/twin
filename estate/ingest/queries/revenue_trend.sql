-- Finance — Revenue Review :: "Net revenue, daily"
select activity_date, net_usd, gross_usd, settled_net_usd
from marts.mart_revenue_daily
order by activity_date desc
limit 90
