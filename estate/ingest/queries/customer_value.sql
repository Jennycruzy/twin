-- Subscription & Retention :: "Lifetime value distribution"
select lifetime_tier,
       count(*) as customer_count,
       avg(lifetime_net_usd) as avg_lifetime_usd,
       percentile_cont(0.5) within group (order by lifetime_net_usd) as median_lifetime_usd
from marts.mart_customer_360
group by lifetime_tier
order by avg_lifetime_usd desc
