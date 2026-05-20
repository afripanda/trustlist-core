# trustlist-dev environment values.
#
# Fill in account_id with the trustlist-dev AWS account ID once the member
# account exists.
account_id = "REPLACE_WITH_DEV_ACCOUNT_ID"

# Dev runs lean to keep cost down: a single-AZ micro instance, a short backup
# window, and no deletion protection so the environment can be torn down freely.
db_instance_class          = "db.t4g.micro"
db_multi_az                = false
db_backup_retention_period = 7
db_deletion_protection     = false
db_skip_final_snapshot     = true
