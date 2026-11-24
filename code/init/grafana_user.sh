#!/bin/bash
# Runs once, on the first start of an empty ClickHouse volume.
# Grafana gets a read-only user that can see the n360 database and nothing else (no n360_next staging).
set -e
clickhouse client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --multiquery <<SQL
CREATE DATABASE IF NOT EXISTS n360;
CREATE USER IF NOT EXISTS grafana IDENTIFIED WITH sha256_password BY '${GRAFANA_CH_PASSWORD}'
    SETTINGS readonly = 1;
GRANT SELECT ON n360.* TO grafana;
SQL
