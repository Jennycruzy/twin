-- Finance — Revenue Review :: "Unreconciled value by processor"
select processor,
       sum(unreconciled_usd) as unreconciled_usd,
       avg(unreconciled_ratio) as avg_unreconciled_ratio,
       avg(authorisation_rate) as avg_authorisation_rate
from marts.mart_finance_reconciliation
group by processor
order by unreconciled_usd desc
