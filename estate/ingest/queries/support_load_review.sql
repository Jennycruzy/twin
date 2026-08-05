-- Ad hoc, support operations. Customers generating the most escalations.
select customer_id, ticket_count, escalated_ticket_count, avg_resolution_hours, avg_csat_score
from intermediate.int_customer_support_load
where escalated_ticket_count > 0
order by escalated_ticket_count desc
limit 50
