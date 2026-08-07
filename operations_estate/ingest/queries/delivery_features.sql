select carrier_id, avg(exception_scan_ratio) as exception_ratio, avg(observed_late) as late_rate
from ops_features.feature_delivery_risk
group by carrier_id
