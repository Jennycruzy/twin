-- Order headers with net amount resolved once, in the merchant's currency.

select
    order_id,
    customer_id,
    merchant_id,
    order_ts,
    order_ts::date                                as order_date,
    status,
    upper(currency_code)                          as currency_code,
    gross_amount,
    discount_amount,
    shipping_amount,
    gross_amount - discount_amount + shipping_amount as net_amount,
    channel,
    (status = 'completed')                        as is_completed

from {{ source('raw_pg', 'orders') }}
