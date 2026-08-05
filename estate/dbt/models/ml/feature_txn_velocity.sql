-- Transaction velocity features at customer grain.
--
-- Velocity is the strongest single predictor the fraud scorer has: a customer whose
-- attempt rate jumps against their own baseline is the classic signature of a
-- compromised account, independent of the amounts involved.

with per_customer_day as (

    select
        customer_id,
        attempted_date,
        count(*)                                  as daily_attempts,
        count(*) filter (where not is_settled)    as daily_declines,
        sum(amount_usd)                           as daily_amount_usd,
        count(distinct merchant_id)               as daily_merchants
    from {{ ref('mart_transaction_enriched') }}
    where customer_id is not null
    group by customer_id, attempted_date

)

select
    customer_id,
    count(*)                                      as active_days,
    sum(daily_attempts)                           as total_attempts,
    sum(daily_declines)                           as total_declines,
    sum(daily_amount_usd)                         as total_amount_usd,
    avg(daily_attempts)                           as avg_daily_attempts,
    max(daily_attempts)                           as max_daily_attempts,
    stddev_pop(daily_attempts)                    as stddev_daily_attempts,
    max(daily_attempts)::numeric
        / nullif(avg(daily_attempts), 0)          as peak_to_mean_attempts,
    avg(daily_amount_usd)                         as avg_daily_amount_usd,
    max(daily_amount_usd)                         as max_daily_amount_usd,
    max(daily_merchants)                          as max_daily_merchants,
    sum(daily_declines)::numeric
        / nullif(sum(daily_attempts), 0)          as decline_rate,
    max(attempted_date)                           as last_attempt_date

from per_customer_day
group by customer_id
