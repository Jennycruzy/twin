-- Growth Funnel :: "Zero-result searches"
select session_date, search_count, zero_result_count,
       zero_result_count::numeric / nullif(search_count, 0) as zero_result_rate
from marts.mart_marketing_funnel
order by session_date desc
limit 60
