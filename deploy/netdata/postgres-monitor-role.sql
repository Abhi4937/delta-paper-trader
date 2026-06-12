-- Read-only monitoring role for Netdata's postgres collector (least privilege:
-- pg_monitor grants read access to pg_stat_* views/functions only, NOT app table data).
-- Run ONCE on the VM, password matching NETDATA_PG_PASSWORD in the VM .env:
--   docker exec -i delta-paper-trader-db-1 \
--     psql -U paper -d paper_trader -v pw="'YOUR_PASSWORD'" \
--     -f - < deploy/netdata/postgres-monitor-role.sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'netdata') THEN
    CREATE ROLE netdata LOGIN;
  END IF;
END $$;
ALTER ROLE netdata PASSWORD :pw;
GRANT pg_monitor TO netdata;
