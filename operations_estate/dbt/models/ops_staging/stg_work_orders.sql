{{ config(meta={'owner': 'diego.mora@example.com', 'team': 'facilities', 'criticality_tier': 'tier2'}) }}

select work_order_id, facility_id, opened_at, closed_at, issue_code, severity, status,
       extract(epoch from (coalesce(closed_at, '{{ var("operations_end_date") }}'::timestamptz) - opened_at)) / 3600.0 as open_hours
from {{ source('ops_erp', 'work_orders') }}
