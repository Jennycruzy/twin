-- One row per payment attempt, with its refund and dispute outcome attached.
--
-- Net settled value subtracts refunds and open disputes, because a payment that settled
-- and was then charged back is not revenue and finance should never see it as such.

with refunds as (

    select
        payment_id,
        sum(amount)                               as refunded_amount,
        count(*)                                  as refund_count
    from {{ ref('stg_refunds') }}
    group by payment_id

), disputes as (

    select
        payment_id,
        sum(amount)                               as disputed_amount,
        count(*)                                  as dispute_count,
        bool_or(is_fraud_dispute)                 as has_fraud_dispute,
        bool_or(is_open)                          as has_open_dispute
    from {{ ref('stg_chargebacks') }}
    group by payment_id

)

select
    p.payment_id,
    p.order_id,
    p.payment_method_id,
    p.attempted_ts,
    p.attempted_ts::date                          as attempted_date,
    p.settled_ts,
    p.status,
    p.processor,
    p.decline_reason,
    p.is_settled,
    p.settle_latency_seconds,
    p.currency_code,
    p.amount,
    fx.rate                                       as fx_rate_to_usd,
    p.amount / fx.rate                            as amount_usd,
    coalesce(r.refunded_amount, 0) / fx.rate      as refunded_amount_usd,
    coalesce(d.disputed_amount, 0) / fx.rate      as disputed_amount_usd,
    coalesce(r.refund_count, 0)                   as refund_count,
    coalesce(d.dispute_count, 0)                  as dispute_count,
    coalesce(d.has_fraud_dispute, false)          as has_fraud_dispute,
    coalesce(d.has_open_dispute, false)           as has_open_dispute,
    case
        when p.is_settled
            then (p.amount - coalesce(r.refunded_amount, 0) - coalesce(d.disputed_amount, 0)) / fx.rate
        else 0
    end                                           as net_settled_usd

from {{ ref('stg_payments') }} p
left join refunds r
    on p.payment_id = r.payment_id
left join disputes d
    on p.payment_id = d.payment_id
left join {{ ref('stg_fx_rates') }} fx
    on fx.rate_date = p.attempted_ts::date
   and fx.quote_currency = p.currency_code
   and fx.base_currency = 'USD'
