-- Refunds against settled payments.

select
    refund_id,
    payment_id,
    refunded_ts,
    refunded_ts::date                             as refund_date,
    amount,
    upper(currency_code)                          as currency_code,
    reason_code

from {{ source('raw_pg', 'refunds') }}
