-- Payment attempts. Declines and retries are both kept: the retry pattern is signal for
-- the fraud features, and dropping declines here would hide the platform's real
-- authorisation rate from finance.

select
    payment_id,
    order_id,
    payment_method_id,
    attempted_ts,
    settled_ts,
    status,
    amount,
    upper(currency_code)                          as currency_code,
    processor,
    decline_reason,
    (status = 'settled')                          as is_settled,
    extract(epoch from (settled_ts - attempted_ts)) as settle_latency_seconds

from {{ source('raw_pg', 'payments') }}
