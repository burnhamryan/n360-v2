# Join memory on ClickHouse 25.8 at real volume

CI runs on small synthetic data, so it cannot see memory problems that only show at real volume. These scripts
check them on **synthetic data scaled past the real size**, on the pinned server (25.8) with the repo's own limits.
Synthetic only: never point `scale_up.py` at a database holding real data.

```bash
docker run -d --name ch258 -p 8123:8123 -e CLICKHOUSE_USER=n360 -e CLICKHOUSE_PASSWORD=ci-loader \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  -v $PWD/code/clickhouse/memory.xml:/etc/clickhouse-server/config.d/n360_memory.xml:ro \
  -v $PWD/code/clickhouse/query_limits.xml:/etc/clickhouse-server/users.d/n360_limits.xml:ro \
  clickhouse/clickhouse-server:25.8
```
```bash
CH_USER=n360 CH_PASSWORD=ci-loader bash code/ci.sh server
```
```bash
python3 code/risk/risk_model.py --user n360 --password ci-loader features --from 2025-01-06 --every 7 --overwrite
```
```bash
CH_USER=n360 CH_PASSWORD=ci-loader python3 code/perf/scale_up.py 4
```
```bash
CH_USER=n360 CH_PASSWORD=ci-loader python3 code/perf/join_bench.py /tmp/queries.json
```
```bash
CH_USER=n360 CH_PASSWORD=ci-loader python3 code/perf/page_load.py /tmp/queries.json hash n360-risk 3
```

On a machine with more RAM than the Mac's Docker VM, cap the server as the Mac does (60% of 7.8 GB): add
`<clickhouse><max_server_memory_usage>4700000000</max_server_memory_usage></clickhouse>` to `config.d`. ClickHouse
sizes its 60% from the host's RAM, not from a `docker run --memory` limit, so without this the container is killed
(exit 137) instead of refusing the query.

## Result, 27 Nov 2026 (25.8.33, 65 customers, 110 VAs, 75k va_day rows, 200k risk features, 4.4 GB server cap)

| Setting | Peak per panel | All 224 panels | Panels failed (code 241) |
|---|---|---|---|
| `parallel_hash` (25.8 default), 10 threads | 2.0 GB (the per-query cap) | 36 GB | 7, all on Risk |
| `parallel_hash`, 4 threads | 2.0 GB | 16 GB | 1 |
| `hash`, 4 threads | 129 MB | 2.3 GB | 0 |
| `hash`, 10 threads | 101 MB | 2.0 GB | 0 |

Rows agree between the settings on every panel that ran under both (the only differences were `now()` on tiles).
Risk page loaded as Grafana does (13 panels at once, 3 times, fresh server): `parallel_hash` peaked at 5.6 GB with
14 of 39 panels refused; `hash` peaked at 0.7 GB with none. After `parallel_hash` queries the server also keeps
about 4.4 GB resident, so later small queries are refused too. Hence `join_algorithm=hash` in
`code/clickhouse/query_limits.xml`.
