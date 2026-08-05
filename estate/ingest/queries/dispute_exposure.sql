-- Finance — Revenue Review :: "Open dispute exposure"
select attempted_date,
       sum(disputed_amount_usd) as disputed_usd,
       count(*) filter (where has_open_dispute) as open_dispute_count
from marts.mart_transaction_enriched
where has_open_dispute
group by attempted_date
order by attempted_date desc
limit 60
