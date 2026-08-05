-- Recurring plans, with annual contracts normalised to a monthly figure so that MRR is
-- comparable across billing intervals.

select
    subscription_id,
    customer_id,
    plan_code,
    started_ts,
    cancelled_ts,
    mrr_amount,
    case
        when billing_interval = 'annual' then mrr_amount / 12.0
        else mrr_amount
    end                                           as normalised_mrr,
    upper(currency_code)                          as currency_code,
    billing_interval,
    (cancelled_ts is null)                        as is_active

from {{ source('raw_pg', 'subscriptions') }}
