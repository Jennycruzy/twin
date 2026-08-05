-- Ad hoc, analytics engineering. Spot-checks currency conversion against a raw order.
select o.order_id, o.order_date, o.currency_code, o.net_amount, o.fx_rate_to_usd, o.net_amount_usd
from intermediate.int_orders_enriched o
where o.currency_code <> 'USD'
order by o.order_date desc
limit 100
