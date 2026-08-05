-- Finance — Revenue Review :: "Authorisation rate"
select activity_date, authorisation_rate, payment_attempt_count, settled_payment_count
from marts.mart_revenue_daily
where payment_attempt_count > 0
order by activity_date desc
limit 90
