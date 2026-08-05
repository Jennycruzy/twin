-- Customer accounts, with soft-deleted rows removed and tier normalised.
-- Downstream models must not have to remember that deleted_at exists.

select
    customer_id,
    lower(email)                                  as email,
    full_name,
    upper(country_code)                           as country_code,
    signup_ts,
    account_status,
    marketing_opt_in,
    coalesce(lifetime_tier, 'none')               as lifetime_tier,
    (account_status = 'active')                   as is_active

from {{ source('raw_pg', 'customers') }}
where deleted_at is null
