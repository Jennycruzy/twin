-- Ad hoc, ML platform. Checks the feature tables the scorer reads are populated and that
-- the risk signal has not collapsed to a constant.
select count(*) as rows,
       count(*) filter (where failed_login_rate > 0) as with_failed_logins,
       avg(min_device_trust) as avg_min_device_trust,
       max(max_customers_sharing_device) as max_shared_device
from ml.feature_customer_risk
