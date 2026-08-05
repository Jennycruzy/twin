-- One row per address. The default shipping address is the one most models want, so it
-- is flagged here rather than re-derived downstream.

select
    address_id,
    customer_id,
    address_type,
    line1,
    city,
    region,
    postal_code,
    upper(country_code)                           as country_code,
    is_default,
    (is_default and address_type = 'shipping')    as is_default_shipping

from {{ source('raw_pg', 'addresses') }}
