-- Product catalogue. Prices stay in the merchant's own currency here; conversion happens
-- in the intermediate layer where the rate join belongs.

select
    product_id,
    merchant_id,
    category_id,
    sku,
    title,
    list_price,
    upper(currency_code)                          as currency_code,
    is_active,
    created_ts

from {{ source('raw_pg', 'products') }}
