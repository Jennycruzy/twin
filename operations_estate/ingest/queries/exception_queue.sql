select facility_id, exception_code, severity, work_order_status
from ops_marts.mart_exception_queue
order by event_ts desc
limit 100
