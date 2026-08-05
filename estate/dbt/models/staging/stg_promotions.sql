-- Promotional campaigns with their active window resolved.

select
    promotion_id,
    promo_code,
    discount_type,
    discount_value,
    starts_ts,
    ends_ts,
    budget_amount,
    extract(epoch from (ends_ts - starts_ts)) / 86400.0 as window_days

from {{ source('raw_pg', 'promotions') }}
