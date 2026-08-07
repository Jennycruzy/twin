{{ config(meta={'owner': 'diego.mora@example.com', 'team': 'facilities', 'criticality_tier': 'tier1'}) }}

select e.event_id, e.facility_id, e.shipment_id, e.event_ts, e.exception_code,
       e.route_code, w.work_order_id, w.severity, w.status as work_order_status
from {{ ref('int_exception_events') }} e
left join {{ ref('stg_work_orders') }} w on w.facility_id = e.facility_id
