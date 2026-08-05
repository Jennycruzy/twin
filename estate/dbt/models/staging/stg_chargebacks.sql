-- Disputes. Open chargebacks carry unresolved financial exposure, so the open/closed
-- split is made explicit here.

select
    chargeback_id,
    payment_id,
    opened_ts,
    resolved_ts,
    amount,
    upper(currency_code)                          as currency_code,
    reason_code,
    liability,
    (resolved_ts is null)                         as is_open,
    (reason_code = 'fraud')                       as is_fraud_dispute

from {{ source('raw_pg', 'chargebacks') }}
