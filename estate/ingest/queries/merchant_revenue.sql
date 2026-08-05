-- Merchant Operations :: "Merchant revenue ranking"
select merchant_id, merchant_name, net_amount_usd, completed_order_count, on_time_rate
from marts.mart_merchant_scorecard
order by net_amount_usd desc nulls last
limit 50
