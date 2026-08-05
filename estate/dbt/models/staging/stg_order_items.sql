-- Order lines with line total resolved.

select
    order_item_id,
    order_id,
    product_id,
    quantity,
    unit_price,
    upper(currency_code)                          as currency_code,
    quantity * unit_price                         as line_amount

from {{ source('raw_pg', 'order_items') }}
