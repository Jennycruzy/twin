-- Marketplace sellers. Suspended merchants are retained: historical orders against them
-- are still real revenue and finance needs them reconciled.

select
    merchant_id,
    merchant_name,
    upper(country_code)                           as country_code,
    onboarded_ts,
    risk_band,
    upper(settlement_currency)                    as settlement_currency,
    is_suspended,
    (risk_band = 'high' or is_suspended)          as needs_review

from {{ source('raw_pg', 'merchants') }}
