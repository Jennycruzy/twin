-- Ad hoc, risk operations. Weekly review of merchants by dispute intensity.
select merchant_id, merchant_name, risk_band, payment_count, dispute_rate, disputed_value_share
from ml.feature_merchant_risk
where payment_count > 20
order by disputed_value_share desc nulls last
limit 40
