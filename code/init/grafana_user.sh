#!/bin/bash
# Runs once, on the first start of an empty ClickHouse volume.
# Grafana gets a read-only user that can see the n360 database and nothing else (no n360_next staging).
# Two locks: the user is granted SELECT only, and readonly = 2 refuses every write and DDL. readonly = 2,
# not 1, because the Grafana ClickHouse plugin sets max_execution_time on each query and readonly = 1
# refuses any setting change (the data source fails its health check). CONST stops a query switching
# readonly itself off. Grafana's chat MCP server (mcp-grafana) reaches data only through this user.
# code/ci.sh runs the SQL below too, so this file is the one definition of the user.
set -e
clickhouse client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --multiquery <<SQL
CREATE DATABASE IF NOT EXISTS n360;
CREATE USER IF NOT EXISTS grafana IDENTIFIED WITH sha256_password BY '${GRAFANA_CH_PASSWORD}'
    SETTINGS readonly = 2 CONST;
GRANT SELECT ON n360.* TO grafana;
SQL
