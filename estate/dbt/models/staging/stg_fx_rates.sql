-- Daily USD-base rates, with an identity row added for USD itself.
--
-- Every currency conversion in the estate resolves through this model. The identity row
-- exists so that downstream joins never need a special case for USD orders, which would
-- otherwise silently drop them when the join misses.

select
    rate_date,
    upper(base_currency)                          as base_currency,
    upper(quote_currency)                         as quote_currency,
    rate,
    source

from {{ source('raw_pg', 'fx_rates') }}

union all

select distinct
    rate_date,
    'USD'                                         as base_currency,
    'USD'                                         as quote_currency,
    1.0                                           as rate,
    'identity'                                    as source

from {{ source('raw_pg', 'fx_rates') }}
