# trustlist-prod environment values.
#
# Fill in account_id with the trustlist-prod AWS account ID once the member
# account exists.
account_id = "REPLACE_WITH_PROD_ACCOUNT_ID"

# Prod runs for resilience (Stage 0 PRD §7c): Multi-AZ, a larger instance, a
# 30-day point-in-time-recovery window, deletion protection on, and a final
# snapshot taken on any teardown.
db_instance_class          = "db.t4g.medium"
db_multi_az                = true
db_backup_retention_period = 30
db_deletion_protection     = true
db_skip_final_snapshot     = false
