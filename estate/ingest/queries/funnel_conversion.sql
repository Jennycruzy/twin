-- Growth Funnel :: "Session conversion funnel"
select session_date, session_count, reached_product_count, reached_cart_count,
       reached_confirm_count, product_to_cart_rate, cart_to_confirm_rate
from marts.mart_marketing_funnel
order by session_date desc
limit 60
