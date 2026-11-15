# Load-test baseline (C-063)

The number later stage reviews compare against. One run of
`./run_one.sh S2 300 default` at the commit stage 0 started from.

| | |
|---|---|
| Commit | `de69a7bb6f0df0a2e157a58f8dfa2ecba4ccb10a` (image `unbork-loadtest:de69a7b`) |
| Host | `tester` (10.10.10.164), reached as lin-manager-2 → `ssh -J lin-manager tester`. Not lin-manager-2 as the spec said: it has 7.75 GB RAM with swap full and 3 GB free disk. Dennis pointed the run at the test hosts on 2026-11-15 |
| Machine | Debian 12, kernel 6.1.0-54-amd64, 4 cores, 15,992 MB RAM (about 15.2 GB available), no swap, 35 GB root with 24 GB free (grown from 10 GB for this run) |
| Docker | 29.9.3, containerd 2.4.2, Compose 5.7.0 |
| Mode | Single host: the 300 fake hosts and unbork share the 4 cores |
| Run | 2026-11-15 20:24:10 to 22:17:15 UTC |
| Window | 21:01:05 to 22:01:05 UTC (60 min, after 10 min warm-up) |
| Seeding | 300 hosts created in 4 s; first sync of all 300 took 1,448 s |
| Result | `results/S2-n300-default: done rc=0 disk_free=23477MiB ooms=0` |
| Raw results | `~/loadtest-baselines/2026-11-15-de69a7b/` on tester (not committed) |

## Commands

From a clone at the commit above:

```bash
docker build -t unbork-loadtest:de69a7b .
cd dev/loadtest
LABDOG_IMAGE=unbork-loadtest:de69a7b ./prepare.sh
LABDOG_IMAGE=unbork-loadtest:de69a7b ./run_one.sh S1 20 default 1 3   # sanity, short window
LABDOG_IMAGE=unbork-loadtest:de69a7b ./run_one.sh S2 300 default      # in tmux
```

The harness at `de69a7b` needed one fix to seed at all: `seed.py`
asked `/api/sync/jobs?limit=1000`, which the route rejects (it caps
`limit` at 500). The run used `limit=500` locally; the same fix is
committed in stage 0 (`290c7d1`).

## measure.txt

```
== drift per module
 module_type | checks | avg_ms | p50_ms | p95_ms | max_ms
-------------+--------+--------+--------+--------+--------
 package     |    250 |   1377 | 1355.5 |   1586 |   1713
 cron        |    255 |    339 |    337 |    359 |    390
 resolver    |    222 |    328 |    324 |  343.9 |    745
 hosts_file  |    300 |    321 |    319 |    339 |    375

== drift status
 module_type | status  | count
-------------+---------+-------
 cron        | in_sync |   255
 hosts_file  | in_sync |   300
 package     | in_sync |   250
 resolver    | in_sync |   222

== sync
 kind | status  | jobs | avg_run_s | p95_run_s | p50_wait_s | p95_wait_s | max_wait_s
------+---------+------+-----------+-----------+------------+------------+------------
 bulk | success |  692 |        18 |        20 |       1093 |       1480 |       1550

== action host runs
 status | host_runs | avg_s | p95_s
--------+-----------+-------+-------

== action runs
 id | parallelism | status | wait_s | run_s
----+-------------+--------+--------+-------

== error samples
 err | count
-----+-------

 err | count
-----+-------

== stuck rows at end
 k | status | count
---+--------+-------
```

## Resource samples over the window

241 samples, one every 10 s, from `samples.csv`. Peak and median
(for `mem_avail_mib`, the peak column is the most memory available).

| Metric | Peak | Median |
|---|---|---|
| unbork memory (MiB) | 1,392 | 1,308 |
| Postgres memory (MiB) | 106 | 99 |
| Redis memory (MiB) | 22 | 14 |
| unbork CPU (%) | 322 | 149 |
| Postgres CPU (%) | 89 | 2.6 |
| queue `default` | 7 | 0 |
| queue `long_running` | 638 | 542 |
| queue `orchestrator` | 0 | 0 |
| queue `drift` | 1,450 | 1,089 |
| Postgres connections | 12 | 12 |
| syncs pending | 296 | 209 |
| syncs running | 4 | 4 |
| action runs / host runs pending or running | 0 | 0 |
| host CPU busy (%) | 95 | 92 |
| load1 | 8.73 | 5.89 |
| memory available (MiB) | 10,629 | 10,266 |
| disk free (MiB) | 23,491 | 23,484 |

Computed with:

```python
import csv, statistics, sys
FROM, TO = "2026-11-15T21:01:05Z", "2026-11-15T22:01:05Z"
rows = [r for r in csv.DictReader(open(sys.argv[1])) if FROM <= r["ts"] <= TO]
for c in ["labdog_mib", "pg_mib", "redis_mib", "labdog_cpu_pct", "pg_cpu_pct",
          "q_default", "q_long_running", "q_orchestrator", "q_drift", "pg_conns",
          "sync_pending", "sync_running", "run_pending", "run_running",
          "hostrun_pending", "hostrun_running", "host_cpu_busy_pct", "load1",
          "mem_avail_mib", "disk_free_mib"]:
    v = [float(r[c]) for r in rows if r[c]]
    print(c, max(v), statistics.median(v))
```

## Notes

- **Sync throughput is the limit.** A bulk sync takes about 18 s and
  only 4 run at once, so the default slots finish about 800 an hour; S2
  asks for 300 every 15 min (1,200 an hour). 692 finished in the window,
  the backlog never cleared (median 209 pending, median wait 18 min,
  p95 25 min), and the scenario log shows 120 to 136 of the 300 hosts
  skipped at each tick because a sync for them was already pending. The
  drift queue backed up too (peak 1,450). This is the baseline, not a
  regression; later stages compare against it.
- **The host CPU was saturated** (92% median), with the fake hosts on
  the same 4 cores. Treat CPU-bound numbers as an upper bound on cost.
- **Memory, Postgres connections and disk** were comfortable.
- **Logs:** 4,392 lines in `labdog-log.gz`, no CRITICAL, Traceback,
  Timeout or WorkerLost. The one ERROR line was a mistyped read-only
  status query typed against the test database during the drain, not
  unbork.
- **Stuck rows at end:** none.
- **Teardown:** no loadtest container, volume or network left on tester.
  A runtime route to `172.30.0.0/16` on tester (left from an earlier
  two-host run, clashing with the harness network) was removed for the
  run and put back after. Nothing was changed on lin-manager or tester2.
- Nothing from `dev/loadtest/results/`, `.state/` or `.env` is committed.
