-- Subscription & Retention :: "Active MRR by plan"
select plan_code, billing_interval, active_count, active_mrr_usd, churn_rate
from marts.mart_subscription_health
order by active_mrr_usd desc nulls last
