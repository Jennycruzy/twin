-- Ad hoc, ML platform. Pulls the velocity tail the scorer is most sensitive to.
select customer_id, total_attempts, max_daily_attempts, peak_to_mean_attempts, decline_rate
from ml.feature_txn_velocity
where peak_to_mean_attempts is not null
order by peak_to_mean_attempts desc
limit 200
